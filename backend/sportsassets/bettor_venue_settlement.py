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
EV_INFERRED = "INFERRED_NOT_ATTESTED"
EV_NONE = "NOT_ESTABLISHED"

#: Only these count. An inference is recorded and does not unblock.
ATTESTING_CLASSES = (EV_VENUE_CATALOGUE, EV_BOOK_PAYLOAD, EV_BOTH_SIDES)


def attest(*, sport_family, market="h2h", venue_evidence=None,
           book_evidence=None) -> dict:
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
    league = ve.get("team_league")
    out["rules"]["overtime"] = {
        "applicable": True, "established": False,
        "evidence_class": (EV_INFERRED if league else EV_NONE),
        "refusal": R_OVERTIME_UNKNOWN,
        "source": ve.get("source") or "us_premap",
        "book_rule": BOOK_SETTLEMENT.get(fam),
        "detail": ("the fixture's competition is %r, and a league fixture "
                   "has no extra time -- so regulation and full-time "
                   "coincide HERE. That is a fact about the competition, "
                   "not about the contract's rule, and a knockout tie "
                   "would break it. Recorded as an inference; it does NOT "
                   "establish the rule" % (league,)) if league else
                  ("no competition is recorded for this fixture and no "
                   "rules text exists on either side")}

    # ── push ─────────────────────────────────────────────────────────
    out["rules"]["push"] = {
        "applicable": mkt != "h2h", "established": True,
        "evidence_class": EV_BOOK_PAYLOAD, "source": "market type",
        "detail": "h2h carries no line, so there is no tie-at-the-line"}

    # ── void ─────────────────────────────────────────────────────────
    out["rules"]["void"] = {
        "applicable": True, "established": False,
        "evidence_class": EV_NONE, "refusal": R_VOID_UNKNOWN,
        "source": "neither side publishes an abandonment rule we hold",
        "detail": VOID_NOTE}

    out["unmet"] = sorted({r["refusal"] for r in out["rules"].values()
                           if r.get("applicable") and not r["established"]
                           and r.get("refusal")})
    out["attested"] = sorted(k for k, r in out["rules"].items()
                             if r["established"]
                             and r.get("evidence_class") in ATTESTING_CLASSES)
    out["overall_established"] = not out["unmet"]
    return out
