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
