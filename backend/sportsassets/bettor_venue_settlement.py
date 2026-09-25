"""WHAT DOES THE VENUE CONTRACT ACTUALLY SETTLE ON? Usually: unknown.

The directive requires matching settlement rules before a probability may
be compared with a price. Meeting that requirement honestly means
admitting how little is established.

WHAT PINNACLE SETTLES ON IS KNOWN PER SPORT, and the two we price differ:

    soccer    full-time 1X2 is REGULATION 90 PLUS STOPPAGE, no extra time
    baseball  the moneyline is the FULL GAME, extra innings included

WHAT THE VENUE CONTRACT SETTLES ON IS NOT IN OUR DATA. The `markets` table
carries condition_id, title, slug, event_title, sport, tags, closed,
resolved and resolved_prices. None of those states a settlement rule. A
title like "Will Fulham beat Hull City?" is consistent with regulation-only
AND with including extra time, and for a knockout fixture those are
different bets with materially different probabilities.

SO THE DEFAULT IS A REFUSAL, NOT AN ASSUMPTION. `rule_for` returns None
unless the rule has been ATTESTED, and the caller refuses by name and
counts it. This follows `bettor_sport_mapping`'s standard exactly: a
mapping is admitted only where the venue's own rows attest it, and
"however obviously true it happens to be" is not attestation.

WHY THAT IS NOT A CLIMBDOWN. A refusal that names its missing input is a
result: it says what to go and get. Asserting a match we have not
established would make every downstream edge number unfalsifiable, and
for league fixtures -- where regulation and full-time coincide almost
always -- it would be right often enough to never look wrong.

TO CLOSE IT, one of:
  * the venue's market-rules text, captured per condition and parsed;
  * a resolved-market study joining resolved_prices to matches decided
    after 90 minutes, which measures the rule instead of reading it.
Both produce ATTESTED entries below. Neither is done, so the table is
empty and says so.
"""

from __future__ import annotations

import re

from . import bettor_settlement_terms as _ST

VERSION = "BETTOR_VENUE_SETTLEMENT_V1"

R_NOT_ESTABLISHED = "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"

#: Pinnacle's side, which IS known, per sport family.
BOOK_SETTLEMENT = {
    "soccer": "REGULATION_90_PLUS_STOPPAGE_NO_EXTRA_TIME",
    "baseball": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
}

# ── the four rules, separately, because only one is even answerable ──
#
# "Settlement rules match" is four questions, not one. Collapsing them
# would let three unknowns hide behind the one that happens to be
# checkable.

R_DRAW_ASYMMETRIC = "DRAW_HANDLING_NOT_RECONCILED"
R_OVERTIME_UNKNOWN = "OVERTIME_RULE_NOT_ESTABLISHED"
R_VOID_UNKNOWN = "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED"
R_PUSH_OUT_OF_SCOPE = "PUSH_NOT_APPLICABLE_TO_H2H"

#: A terminal rule that the venue's own prose CONTRADICTS. This is louder
#: than "not established": the two sides settle differently and a
#: probability from one cannot price a contract on the other.
R_OVERTIME_CONFLICTS = "OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE"
R_VOID_BOOK_RULE_NOT_HELD = "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD"

#: DECLARED PROSE PATTERNS, per sport family, for the one terminal rule a
#: venue contract's own text can settle. They are deliberately narrow:
#: only an EXPLICIT statement about extra innings / extra time attests.
#: A phrase like "final result" is left to the inference branch, because
#: reading it as "extra innings included" is a fact about the sport, not
#: about the contract.
#:
#: `includes` must AGREE with the book rule for the family; `excludes`
#: contradicts it. A text matching both is a CONFLICT, never a pass.
OVERTIME_PROSE = {
    "baseball": {
        "book_rule_includes_overtime": True,
        "includes": (r"includ\w*\s+(?:any\s+)?extra\s+innings",
                     r"extra\s+innings\s+(?:are|will\s+be|shall\s+be)"
                     r"\s+includ",
                     r"includ\w*\s+extra\s+time"),
        "excludes": (r"exclud\w*\s+(?:any\s+)?extra\s+innings",
                     r"(?:only|first)\s+nine\s+innings",
                     r"after\s+nine\s+innings\s+only",
                     r"regulation\s+(?:nine\s+)?innings\s+only"),
    },
    "soccer": {
        "book_rule_includes_overtime": False,
        "includes": (r"(?:90|ninety)\s+minutes",
                     r"regulation\s+time\s+only",
                     r"exclud\w*\s+extra\s+time",
                     r"(?:does\s+)?not\s+includ\w*\s+extra\s+time"),
        "excludes": (r"includ\w*\s+extra\s+time",
                     r"penalt(?:y|ies)\s+shoot",
                     r"including\s+any\s+extra\s+time"),
    },
}

#: THE BOOKMAKER'S ABANDONMENT RULE, PER FAMILY -- DELIBERATELY EMPTY.
#:
#: A value here is a claim about a third party's published terms. It may be
#: added ONLY from the bookmaker's own rules page, with the citation in the
#: commit that adds it, and never from recollection. While it is empty the
#: void rule cannot be established for any fixture and every hold value is
#: reported CONDITIONAL on it -- which is the truthful state, not a bug.
#:
#: Values are the term CLASSES below, so the two sides are compared on what
#: happens rather than on wording.
BOOK_VOID_RULE: dict = {}

#: What an abandonment term can say. Compared class-to-class.
VOID_REFUND = "STAKE_REFUNDED_MARKET_VOID"
VOID_RESOLVES_NO = "RESOLVES_NO_FOR_THE_HELD_SIDE"
VOID_STAYS_OPEN = "REMAINS_OPEN_UNTIL_REPLAYED"

R_VOID_CONFLICTS = "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE"

#: Declared prose patterns for each class, venue side.
VOID_PROSE = (
    (VOID_REFUND, (r"\bvoid", r"refund", r"\bcancel\w*\s+and\s+refund",
                   r"stakes?\s+(?:are\s+)?return")),
    (VOID_RESOLVES_NO, (r"resolve[sd]?\s+(?:to\s+)?no\b",
                        r"settle[sd]?\s+(?:as\s+)?no\b")),
    (VOID_STAYS_OPEN, (r"remain\w*\s+open", r"until\s+(?:it\s+is\s+)?"
                       r"(?:replayed|completed|resumed)",
                       r"market\s+stays\s+open")),
)


def _void_class(text: str):
    """Which abandonment class this prose states, or None.

    SUPERSEDED as the compatibility test. It answers "which word does this
    document use", and the words are not the rule: the same phrase can
    attach to a different CONDITION on each side. `attest` now compares
    `bettor_settlement_terms` condition -> payout pairs instead. Kept
    because the class names remain the legacy vocabulary of
    `BOOK_VOID_RULE`, which is translated into a term below.
    """
    hits = [cls for cls, pats in VOID_PROSE
            if any(re.search(pp, text) for pp in pats)]
    if len(hits) != 1:
        return (None, hits)
    return (hits[0], hits)


#: The legacy single-class vocabulary, mapped onto declared payouts.
LEGACY_CLASS_TO_PAYOUT = {
    VOID_REFUND: _ST.PAY_STAKE_BACK,
    VOID_RESOLVES_NO: _ST.PAY_NO,
    VOID_STAYS_OPEN: _ST.PAY_LATER,
}


def _legacy_book_terms(fam) -> dict:
    """`BOOK_VOID_RULE` as a term set. It speaks to ONE condition.

    A single "the book voids an abandoned fixture" class says nothing about
    a game STOPPED EARLY or one MADE OFFICIAL SHORT, and those are exactly
    the conditions on which a money line's action turns. So holding the
    legacy class can contribute a match at
    `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` and can never by itself
    make the comparison COMPATIBLE. That is a correction, not a
    regression: the old code called it established.
    """
    pay = LEGACY_CLASS_TO_PAYOUT.get(BOOK_VOID_RULE.get(fam))
    if pay is None:
        return {}
    return {_ST.C_NOT_PLAYED: {
        "payout": pay,
        "cite": {"source": "BOOK_VOID_RULE (legacy single-class hook)",
                 "source_url": "", "retrieved_at": "", "quote": ""}}}


#: DOES THE VENUE PUBLISH PER-CONDITION SETTLEMENT TERMS ANYWHERE?
#:
#: WHY THIS MATTERS MORE THAN ANY OTHER GAP IN THE ENTRY LANE.
#: VOID_ABANDONMENT_RULE_NOT_ESTABLISHED is carried by 464 of 464
#: candidates evaluated in 24 hours -- the only refusal with full
#: coverage. `bettor_settlement_terms.compare` returns COMPATIBLE only
#: when EVERY applicable condition is stated by BOTH sides, and
#: `silence_is_not_agreement` is True by design. Seven conditions apply to
#: a baseball money line. The BOOK side is captured with citations. The
#: only VENUE prose held is the listing's `description` field, measured at
#: 380 characters, which states one condition. Six stay silent, so the
#: verdict is UNKNOWN and every candidate refuses.
#:
#: That is structural, not a parsing failure: a 380-character blurb cannot
#: state seven conditions. So the question is whether a per-condition
#: document exists to read at all. It was asked, from a GitHub runner with
#: ordinary egress, because the development container's policy denies these
#: hosts -- the same reason CAPTURE_ATTEMPTS had to run there.
#:
#: RECORDED AS FACTS, NOT AS THE MEMORY OF A FAILED COMMAND. A 404 answers
#: "does the venue publish this" as definitely as a 200 would, and a
#: retrieval nobody wrote down has to be repeated by the next person.
VENUE_TERMS_CAPTURE_ATTEMPTS = (
    {"asked_at": "2026-09-25T14:42:46Z",
     "reader": "github-actions runner (ubuntu-latest, ordinary egress)",
     "via": "command-verify capture_terms, terms_url",
     "targets_404": ("https://docs.polymarket.us/settlement",
                     "https://docs.polymarket.us/rules",
                     "https://docs.polymarket.us/market-rules",
                     "https://docs.polymarket.us/resolution",
                     "https://docs.polymarket.us/sports-rules"),
     "result": "HTTP_404",
     "detail": ("five candidate paths, every one 404. The body served with "
                "the 404 is 117 KB of documentation-site shell, which is "
                "why a byte count alone would have looked like a hit")},
    {"asked_at": "2026-09-25T14:42:49Z",
     "target": "https://docs.polymarket.us/",
     "result": "HTTP_200_NO_SETTLEMENT_PROSE",
     "detail": ("406 words extracted and ZERO matched the settlement "
                "keyword set -- not `void`, not `abandon`, not `graded`, "
                "not `innings`. The documentation root does not discuss "
                "settlement conditions")},
    {"asked_at": "2026-09-25T14:42:50Z",
     "targets": ("https://polymarket.us/rules",
                 "https://polymarket.us/terms"),
     "result": "HTTP_200_CLIENT_RENDERED",
     "detail": ("236 KB of HTML each, 80 words of prose each, and the same "
                "80 words both times: a sign-up offer. The extractor's own "
                "LIKELY_CLIENT_RENDERED branch refused to call it a "
                "capture, correctly -- the rules are not in the served "
                "document")},
)

VENUE_TERMS_NOT_PUBLISHED = (
    "across eight candidate URLs on two venue hosts, NO per-condition "
    "settlement document was retrievable: five 404s, a documentation root "
    "with no settlement vocabulary at all, and two client-rendered shells. "
    "This is an EXTERNAL DEPENDENCY on the venue publishing its grading "
    "rules somewhere a reader can reach, or serving them through an API. "
    "It is not a defect in this repository and it is not cleared by "
    "reading the listing again. Until it is resolved the void rule stays "
    "NOT ESTABLISHED and every supported-market entry candidate refuses -- "
    "which is the truthful state")

VOID_BOOK_NOTE = (
    "the BOOKMAKER's abandonment rule is not recorded in this repository. "
    "The venue's own prose can now be read, so when it states a rule the "
    "remaining gap is OUR record of the book's side -- which must be "
    "captured from the bookmaker's published terms, not asserted here. "
    "Until then this rule is NOT ESTABLISHED and the hold value is "
    "conditional on it")

#: How many outcomes the BOOK prices, per sport family. Soccer h2h is
#: three-way; MLB is two-way.
BOOK_OUTCOMES = {"soccer": 3, "baseball": 2}

#: THE DRAW ASYMMETRY, which is the one that would quietly cost money.
#:
#: A 3-way de-vig over {home, draw, away} yields p(home WINS). The venue
#: contract is binary -- "will X beat Y" -- and a draw resolves it NO. So
#: the probability and the contract actually agree on soccer: both are
#: "home wins outright". That is the good case.
#:
#: What is NOT established is whether every venue soccer contract is that
#: shape. `markets` carries a title and nothing about resolution, so a
#: "double chance" or "X or draw" contract would read as a plain fixture
#: title and take the same 3-way probability against a materially
#: different payout. There is no field to tell them apart, so the pairing
#: is refused rather than assumed.
DRAW_NOTE = (
    "a 3-way de-vig gives p(home wins outright), which is what a binary "
    "'will X beat Y' contract pays on -- but nothing in `markets` "
    "distinguishes that contract from a double-chance one carrying the "
    "same fixture title, so the shape is not established per contract")

VOID_NOTE = (
    "an abandoned or postponed fixture voids at the book. Whether the "
    "venue contract voids, resolves NO, or stays open is not in our data, "
    "and the three differ by the whole stake")

OVERTIME_NOTE = (
    "soccer full-time excludes extra time and the MLB moneyline includes "
    "extra innings, so the rule is per sport on the book side and unknown "
    "on the venue side")


def rules_status(*, sport_family, market="h2h") -> dict:
    """Each settlement rule separately, with what is known about it.

    Returns a dict per rule carrying `established` (bool) and a refusal
    code where it is not. `overall_established` is True only when every
    applicable rule is established -- an unknown is never a match.
    """
    fam = str(sport_family)
    book = BOOK_SETTLEMENT.get(fam)
    venue = rule_for(sport_family=fam, market=market)
    n = BOOK_OUTCOMES.get(fam)

    rules = {
        "draw": {
            "applicable": n == 3,
            "established": False if n == 3 else True,
            "refusal": (R_DRAW_ASYMMETRIC if n == 3 else None),
            "note": (DRAW_NOTE if n == 3
                     else "this sport prices no draw, so nothing to reconcile"),
            "book_outcomes": n,
        },
        "overtime": {
            "applicable": True,
            "established": bool(book is not None and venue is not None),
            "refusal": (None if (book and venue) else R_OVERTIME_UNKNOWN),
            "book_rule": book, "venue_rule": venue, "note": OVERTIME_NOTE,
        },
        "push": {
            "applicable": str(market) != "h2h",
            "established": True,
            "refusal": None,
            "note": ("h2h has no line, so there is no tie-at-the-line to "
                     "push. Spreads and totals are not priced here"),
        },
        "void": {
            "applicable": True,
            "established": bool(venue is not None),
            "refusal": (None if venue is not None else R_VOID_UNKNOWN),
            "note": VOID_NOTE,
        },
    }
    unmet = sorted({r["refusal"] for r in rules.values()
                    if r["applicable"] and not r["established"]
                    and r["refusal"]})
    return {"sport_family": fam, "market": str(market),
            "rules": rules, "unmet": unmet,
            "overall_established": not unmet}

#: The venue's side. DELIBERATELY EMPTY. Each entry, when it exists, must
#: name the evidence that established it -- not a belief about how the
#: venue probably behaves.
#:
#:   ATTESTED[(sport_family, market)] = (rule, evidence)
ATTESTED: dict = {}

#: Recorded so that the emptiness above reads as a finding rather than an
#: oversight, and so a reviewer can see what would fill it.
WHY_EMPTY = (
    "no venue settlement-rule evidence has been captured. The markets "
    "table carries no rules text, and no resolved-market study "
    "distinguishing regulation from full-time outcomes has been run")

HOW_TO_ESTABLISH = (
    "capture the venue's per-market rules text alongside the condition, "
    "or measure the rule from resolved_prices on fixtures decided after "
    "90 minutes")


def rule_for(*, sport_family, market="h2h"):
    """The ATTESTED venue settlement rule, or None. Never a guess."""
    entry = ATTESTED.get((str(sport_family), str(market)))
    return None if entry is None else entry[0]


def agrees(*, sport_family, market="h2h"):
    """Do the two sides settle on the same thing?

    Returns {"agrees": bool|None, ...}. `None` means NOT ESTABLISHED, and
    the caller must treat that as a refusal rather than as permission --
    an unknown is not a match.
    """
    book = BOOK_SETTLEMENT.get(str(sport_family))
    venue = rule_for(sport_family=sport_family, market=market)
    if book is None:
        return {"agrees": None, "refusal": R_NOT_ESTABLISHED,
                "book_rule": None, "venue_rule": venue,
                "why": "this sport family has no declared book rule"}
    status = rules_status(sport_family=sport_family, market=market)
    if venue is None or not status["overall_established"]:
        return {"agrees": None, "refusal": R_NOT_ESTABLISHED,
                "book_rule": book, "venue_rule": venue,
                "why": WHY_EMPTY, "how_to_establish": HOW_TO_ESTABLISH,
                # ALL FOUR RULES, so the refusal names which are missing
                # rather than implying one blanket unknown.
                "rules": status["rules"], "unmet": status["unmet"]}
    return {"agrees": bool(book == venue), "refusal": None,
            "book_rule": book, "venue_rule": venue,
            "rules": status["rules"], "unmet": [],
            "evidence": ATTESTED[(str(sport_family), str(market))][1]}


def describe() -> dict:
    return {
        "version": VERSION,
        "book_settlement": dict(BOOK_SETTLEMENT),
        "venue_settlement_attested": {"%s/%s" % k: v[0]
                                      for k, v in ATTESTED.items()},
        "venue_rules_established": len(ATTESTED),
        "default": "REFUSE",
        "why_empty": WHY_EMPTY,
        "how_to_establish": HOW_TO_ESTABLISH,
        "an_unknown_is_not_a_match": True,
        "rules_checked_separately": ["draw", "overtime", "push", "void"],
        "soccer": rules_status(sport_family="soccer"),
        "baseball": rules_status(sport_family="baseball"),
    }

# ── PER-FIXTURE ATTESTATION FROM EACH SIDE'S OWN CATALOGUE ───────────
#
# A third closure route, cheaper than the two above and available now.
# The module header names two ways to establish the venue's rule: capture
# its rules text, or measure the rule from resolved prices. There is a
# third, and it closes exactly ONE of the four questions:
#
#   THE DRAW. The venue's own catalogue carries a SEPARATE DRAW CONTRACT
#   for soccer fixtures -- `workers/premap` matches it by name
#   (`_yn_draw_row`, slug tail `draw`, `pmus._YN_DRAW_Q_RE`). If the venue
#   lists "will A beat B", "will B beat A" AND a draw contract for one
#   event, then the binary cannot be paying on a draw: the draw is a
#   different contract. Pinnacle's side is visible in the payload we
#   already hold -- a 3-outcome h2h prices Draw as its own outcome. Both
#   sides therefore treat a draw as a separately-settled third result,
#   and that is an attestation from evidence rather than an assumption.
#
# WHAT THIS DOES NOT CLOSE. Overtime and void stay unestablished. For a
# LEAGUE fixture there is no extra time, so regulation and full-time
# coincide -- but that is a fact about the competition, not about the
# contract's rule, and a knockout tie would break it. It is recorded as
# an INFERENCE and does not count. Void/abandonment evidence does not
# exist on either side.

EV_VENUE_CATALOGUE = "ATTESTED_FROM_VENUE_CATALOGUE"
EV_BOOK_PAYLOAD = "ATTESTED_FROM_BOOKMAKER_PAYLOAD"
EV_BOTH_SIDES = "ATTESTED_BOTH_SIDES_INDEPENDENTLY"
#: The venue's OWN published contract prose, fetched from its listing.
#: An attesting class: it is the venue stating its own settlement rule.
EV_VENUE_RULES_TEXT = "ATTESTED_FROM_VENUE_PUBLISHED_RULES_TEXT"
EV_INFERRED = "INFERRED_NOT_ATTESTED"
EV_NONE = "NOT_ESTABLISHED"

#: Only these count. An inference is recorded and does not unblock.
ATTESTING_CLASSES = (EV_VENUE_CATALOGUE, EV_BOOK_PAYLOAD,
                     EV_BOTH_SIDES, EV_VENUE_RULES_TEXT)


def attest(*, sport_family, market="h2h", venue_evidence=None,
           book_evidence=None, observed_at=None, start_at=None,
           start_evidence=_ST.SE_SCHEDULED_CATALOGUE,
           quote_is_in_play=None, book_context=None,
           phase=None, game_format=None) -> dict:
    """Per-rule status with its EVIDENCE CLASS and SOURCE, per fixture.

    `venue_evidence`  what the venue's own catalogue shows for this event,
                      e.g. {"draw_contract_present": True,
                            "draw_slug": "...-draw", "source": "us_premap",
                            "sides_present": [...]}
    `book_evidence`   what the bookmaker's payload shows, e.g.
                      {"outcome_names": ["Home","Away","Draw"],
                       "source": "theoddsapi:h2h"}

    Nothing here sets a boolean on a belief: every `established` True is
    accompanied by the class and the source that established it, and an
    absence of evidence is `NOT_ESTABLISHED`, never `True`.
    """
    fam, mkt = str(sport_family), str(market)
    ve = dict(venue_evidence or {})
    be = dict(book_evidence or {})
    n_book = BOOK_OUTCOMES.get(fam)
    names = [str(x).strip().lower() for x in (be.get("outcome_names") or [])]
    book_prices_draw = any(n == "draw" for n in names) or n_book == 3
    venue_has_draw = bool(ve.get("draw_contract_present"))

    out = {"version": VERSION, "sport_family": fam, "market": mkt,
           "rules": {}, "unmet": [], "attested": [],
           "venue_evidence": ve, "book_evidence": be}

    # ── draw ─────────────────────────────────────────────────────────
    if n_book != 3:
        out["rules"]["draw"] = {
            "applicable": False, "established": True,
            "evidence_class": EV_BOOK_PAYLOAD,
            "source": be.get("source") or "BOOK_OUTCOMES",
            "detail": "this sport prices no draw, so nothing to reconcile"}
    elif venue_has_draw and book_prices_draw:
        out["rules"]["draw"] = {
            "applicable": True, "established": True,
            "evidence_class": EV_BOTH_SIDES,
            "source": "%s + %s" % (ve.get("source") or "us_premap",
                                   be.get("source") or "h2h payload"),
            "detail": ("the venue lists a SEPARATE draw contract (%s) for "
                       "this event, so its binary does not pay on a draw; "
                       "the bookmaker prices Draw as its own outcome. Both "
                       "settle a draw as a third result"
                       % (ve.get("draw_slug") or "slug not recorded")),
            "venue_draw_slug": ve.get("draw_slug")}
    else:
        missing = ("the venue's catalogue shows no separate draw contract "
                   "for this event" if not venue_has_draw else
                   "the bookmaker payload does not price a draw outcome")
        out["rules"]["draw"] = {
            "applicable": True, "established": False,
            "evidence_class": EV_NONE, "refusal": R_DRAW_ASYMMETRIC,
            "source": ve.get("source") or "us_premap",
            "detail": missing}

    # ── overtime ─────────────────────────────────────────────────────
    #
    # THE VENUE'S OWN PROSE IS NOW READ. Until this change the detail said
    # "no rules text exists on either side", which was true of what we
    # HELD and false of what the venue publishes: its listing carries
    # `description` and `assetPriceTerms`. `bettor_live_read.read_rules_text`
    # fetches them; the caller passes the result in as `rules_text`, and
    # this matches it against DECLARED patterns for the family.
    #
    # Three outcomes, never two: the prose AGREES with the book rule
    # (established), CONTRADICTS it (a conflict, which is louder than
    # unknown), or says nothing about the terminal case (unchanged).
    league = ve.get("team_league")
    prose = str(ve.get("rules_text") or "")
    pats = OVERTIME_PROSE.get(fam) or {}
    low = " ".join(prose.lower().split())
    # THE CONDITION -> PAYOUT COMPARISON, COMPUTED ONCE and consulted by
    # both the overtime and the void branch. The overtime rule is one
    # terminal condition among several, so a payout mismatch found there
    # has to reach the overtime verdict too rather than being reported
    # only under "void".
    cmp_ = _ST.compare_prose(sport_family=fam, market=mkt,
                             venue_prose=prose,
                             extra_book_terms=_legacy_book_terms(fam),
                             observed_at=observed_at, start_at=start_at,
                             start_evidence=start_evidence,
                             quote_is_in_play=quote_is_in_play,
                             context=book_context, phase=phase,
                             game_format=game_format)
    _ot_cmp = ((cmp_.get("per_condition") or {}).get(_ST.C_OVERTIME) or {})
    _ot_mismatch = _ot_cmp.get("verdict") == _ST.V_MISMATCH
    hits_inc = [p for p in (pats.get("includes") or ())
                if re.search(p, low)]
    hits_exc = [p for p in (pats.get("excludes") or ())
                if re.search(p, low)]
    ot = {"applicable": True, "book_rule": BOOK_SETTLEMENT.get(fam),
          "venue_rules_text_read": bool(prose),
          "venue_rules_field": ve.get("rules_field"),
          "venue_rules_source": ve.get("rules_source"),
          "matched_includes": hits_inc, "matched_excludes": hits_exc,
          "payout_comparison": _ot_cmp}
    if _ot_mismatch:
        ot.update(established=False, evidence_class=EV_NONE,
                  refusal=R_OVERTIME_CONFLICTS,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("under %s the book pays %r and the venue pays "
                          "%r. The disagreement is in the PAYOUT, not in "
                          "the wording, so no reading of the prose "
                          "reconciles it"
                          % (_ST.C_OVERTIME, _ot_cmp.get("book_payout"),
                             _ot_cmp.get("venue_payout"))))
    elif hits_inc and hits_exc:
        ot.update(established=False, evidence_class=EV_NONE,
                  refusal=R_OVERTIME_CONFLICTS,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the venue's own prose matches BOTH an including "
                          "and an excluding pattern for the terminal case, "
                          "so it does not state one rule. Two readings of "
                          "the same text cannot establish compatibility"))
    elif hits_exc:
        ot.update(established=False, evidence_class=EV_NONE,
                  refusal=R_OVERTIME_CONFLICTS,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the venue's prose states a terminal rule that "
                          "CONTRADICTS the book rule %r for this family. "
                          "The probability prices one event and the "
                          "contract pays on another"
                          % (BOOK_SETTLEMENT.get(fam),)))
    elif hits_inc:
        ot.update(established=True, evidence_class=EV_VENUE_RULES_TEXT,
                  refusal=None,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the venue's OWN published prose states the "
                          "terminal case the same way the book rule %r "
                          "does, matched on %d declared pattern(s)"
                          % (BOOK_SETTLEMENT.get(fam), len(hits_inc))))
    elif prose:
        ot.update(established=False, evidence_class=EV_NONE,
                  refusal=R_OVERTIME_UNKNOWN,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the venue publishes rules prose for this "
                          "contract and it says nothing about the terminal "
                          "case, so the rule is still not established. The "
                          "text was READ, which is why this is now a fact "
                          "about the prose rather than about our records"))
    else:
        ot.update(established=False,
                  evidence_class=(EV_INFERRED if league else EV_NONE),
                  refusal=R_OVERTIME_UNKNOWN,
                  source=ve.get("source") or "us_premap",
                  detail=("the fixture's competition is %r, and a league "
                          "fixture has no extra time -- so regulation and "
                          "full-time coincide HERE. That is a fact about "
                          "the competition, not about the contract's rule, "
                          "and a knockout tie would break it. Recorded as "
                          "an inference; it does NOT establish the rule"
                          % (league,)) if league else
                         ("no competition is recorded for this fixture and "
                          "no venue rules text was read"))
    out["rules"]["overtime"] = ot

    # ── push ─────────────────────────────────────────────────────────
    out["rules"]["push"] = {
        "applicable": mkt != "h2h", "established": True,
        "evidence_class": EV_BOOK_PAYLOAD, "source": "market type",
        "detail": "h2h carries no line, so there is no tie-at-the-line"}

    # ── void ─────────────────────────────────────────────────────────
    #
    # NEVER ESTABLISHED TODAY, and now for a NAMED reason. If the venue's
    # prose states an abandonment rule, the remaining gap is OUR record of
    # the bookmaker's side, which is not in this repository and is not
    # asserted here from memory. Distinguishing the two is the difference
    # between "nobody publishes this" and "we have not captured one side".
    # MATCHED ON CONDITION -> PAYOUT, NOT ON SHARED WORDS. The previous
    # version classified each side's prose into one of three "void
    # classes" and called the rule established when the classes were
    # equal. Two documents can both say "void" and "stakes returned" and
    # still pay differently, because the phrase attaches to a DIFFERENT
    # CONDITION on each side -- the book conditions its money-line action
    # on a MINIMUM NUMBER OF INNINGS and the venue need not. So the
    # comparison is now per terminal condition, and a payout stated for
    # one condition is no evidence about another.
    venue_terms = dict((cmp_.get("venue_read") or {}).get("terms") or {})
    vd = {"applicable": True,
          "venue_rules_text_read": bool(prose),
          "venue_states_a_rule": bool(venue_terms),
          "venue_terms": venue_terms,
          "book_rule_held": bool(cmp_.get("book_terms_held")),
          "compared_on": "CONDITION_TO_PAYOUT",
          "terms_comparison": cmp_,
          "applicable_conditions": cmp_.get("applicable_conditions"),
          "mismatched_conditions": cmp_.get("mismatched_conditions"),
          "unstated_conditions": cmp_.get("unstated_conditions")}
    verdict = cmp_.get("verdict")
    if verdict == _ST.INCOMPATIBLE:
        vd.update(established=False, evidence_class=EV_NONE,
                  refusal=R_VOID_CONFLICTS,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the two sides state DIFFERENT PAYOUTS for the "
                          "same terminal condition(s) %s. An affected "
                          "fixture settles differently on each side and "
                          "the whole stake is the difference, so a "
                          "probability of the book's event cannot price "
                          "this contract"
                          % (cmp_.get("mismatched_conditions"),)))
    elif verdict == _ST.COMPATIBLE:
        vd.update(established=True, evidence_class=EV_BOTH_SIDES,
                  refusal=None,
                  source="%s + captured bookmaker terms"
                         % (ve.get("rules_source") or "venue rules text",),
                  detail=("every applicable terminal condition is stated on "
                          "BOTH sides and the payouts agree condition by "
                          "condition: %s"
                          % (sorted(cmp_.get("per_condition") or {}),)))
    elif not venue_terms:
        # NEITHER SIDE. Distinguished from "we have not captured one side":
        # the venue published no rule for any terminal condition either, so
        # naming only our gap would overstate what exists to be captured.
        vd.update(established=False, evidence_class=EV_NONE,
                  refusal=R_VOID_UNKNOWN,
                  source="neither side publishes an abandonment rule we hold",
                  detail=VOID_NOTE)
    elif not cmp_.get("book_terms_held") or any(
            (r.get("verdict") == _ST.V_BOOK_SILENT
             or r.get("verdict") == _ST.V_BOTH_SILENT)
            for r in (cmp_.get("per_condition") or {}).values()):
        vd.update(established=False, evidence_class=EV_NONE,
                  refusal=R_VOID_BOOK_RULE_NOT_HELD,
                  source=("the bookmaker's terms for this market type are "
                          "not captured in this repository"),
                  detail=VOID_BOOK_NOTE,
                  capture_request=cmp_.get("book_capture_request"))
    else:
        vd.update(established=False, evidence_class=EV_NONE,
                  refusal=R_VOID_UNKNOWN,
                  source=ve.get("rules_source") or "venue rules text",
                  detail=("the bookmaker's terms are held but the venue's "
                          "prose states no rule for %s, so the payouts "
                          "cannot be compared there"
                          % (cmp_.get("unstated_conditions"),)))
    out["rules"]["void"] = vd
    out["terms_comparison"] = cmp_

    out["unmet"] = sorted({r["refusal"] for r in out["rules"].values()
                           if r.get("applicable") and not r["established"]
                           and r.get("refusal")})
    out["attested"] = sorted(k for k, r in out["rules"].items()
                             if r["established"]
                             and r.get("evidence_class") in ATTESTING_CLASSES)
    out["overall_established"] = not out["unmet"]
    return out
