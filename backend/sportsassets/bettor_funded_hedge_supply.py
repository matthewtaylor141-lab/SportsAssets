"""THE PRODUCTION SUPPLIERS FOR AN INDIRECT HEDGE'S LEGS.

`bettor_funded_pair_cycle.discover` takes `held_leg` and `candidate_legs` and
has never been given either in production: `funded_pair_inputs` reported them
NOT WIRED and the discovery step was skipped every cycle. This module is the
wiring, and it is deliberately a separate module because the interesting part
is not plumbing -- it is which facts the catalogue can and cannot supply.

── WHAT A LEG COSTS, AND WHY IT IS NOT A LOOKUP ──────────────────────

`bettor_indirect_structures.Leg` requires eleven STATED facts and says so:

    Every field is required to be stated. `None` on a field this leg's kind
    needs is a refusal, not a default: an unstated overtime rule or an
    unstated orientation makes the payout function unknowable, and a
    structure built on an unknowable leg is a fabricated hedge.

`discover` then matches legs on `grading_key()` -- fixture, period, variable,
overtime treatment. Two legs differing on ANY of those are graded against
DIFFERENT random variables however similar their titles read. So a supplier
that defaulted `overtime`, or guessed which participant a contract backs,
would be manufacturing the settlement compatibility the whole module exists
to refuse. Every field here is either read from a named source or refused.

── WHAT THE CATALOGUE ACTUALLY CARRIES (READ, NOT ASSUMED) ───────────

Two runs of the authorized read-only route each refuted the mapping this
module had been written against. Both refutations are recorded because both
were the same mistake -- deriving a graded fact from a column that looked
like it carried it.

RUN 262:
  * `us_premap.kind` is the single literal 'side' on all 60,540 rows. It does
    NOT name the market type. A supplier reading `kind` would have built
    every leg as one kind, silently.
  * `line` is TEXT. A `Fraction` from free text needs a refusal for the
    unparseable case, not a cast that raises on the decision path.
  * `side_norm` is 'yes'/'no' (23,156 rows each), 'over'/'under' (4,664
    each), or a participant name (4,900 on the `aec-` family).

RUN 263, which refuted the replacement:
  * an `aec-` MONEYLINE row's `side_norm` is the TEAM NAME -- 'doosan bears',
    'broncos' -- not yes/no. So "a yes/no binary with no handicap is a
    moneyline" refuses every real moneyline, which is every position this
    lane has ever held.
  * that same moneyline row CARRIES A LINE: 20, 30, 00, 55. It is not a
    handicap. "A line is stated, so this is a spread" would have built every
    moneyline as a spread at line 20 -- a leg graded against the wrong
    variable, reached by reading a populated column.
  * BOTH SIDES of an `aec-` moneyline carry the SAME `market_slug`
    (`aec-kbo-dbo-ndc-2026-10-01` for Doosan and for NC). The side is carried
    only by `intent`. So the opposite side of a held moneyline is not a
    second settling holding -- it is netting on one instrument, and a
    candidate sharing the held slug is refused as such. That is now a
    measured fact about the venue rather than an assertion.
  * `event_slug` carries no family prefix: it is league, the two participant
    codes in listed order, then the date (`nfl-lar-den-2026-09-27`,
    `kbo-dbo-ndc-2026-10-01`). The `aec-`/`asc-` prefix is on `market_slug`.
  * ZERO funded intents exist. The held_leg supplier therefore has no
    production row to run against today, which is why the integration proofs
    that need a held position cannot be produced from production data.

What DOES name the kind and the period is `sports_type`:
`football_team_full_game_winner` is a full-game moneyline,
`football_team_first_half_spread` is a first-half spread. So the derivation
reads that, against an allowlist assembled from the vocabulary in full --
because a list assembled from memory already broke this lane once, when the
realism classifier invented twelve families and omitted `ufc_` and `darts_`.

`LEG_FIELD_SOURCES` is the provenance table: every field, its source, and the
refusal that fires when the source is silent.
"""

from __future__ import annotations

import re
from fractions import Fraction

from . import bettor_indirect_structures as IS
from . import bettor_settlement_clauses as SETTLE
from . import bettor_venue_settlement as VS

VERSION = "FUNDED_HEDGE_SUPPLY_V1"

# ═════════════════════════════════════════════════════════════════════
# REFUSALS. Each one names a fact that was not stated, never a default.
# ═════════════════════════════════════════════════════════════════════

R_NO_CATALOGUE_ROW = "THE_VENUE_CATALOGUE_HAS_NO_ROW_FOR_THIS_CONTRACT"
R_NO_EVENT = "THE_CATALOGUE_ROW_NAMES_NO_EVENT_SO_NO_FIXTURE_IS_IDENTIFIED"
R_KIND_NOT_DERIVABLE = "THE_MARKET_KIND_IS_NOT_DERIVABLE_FROM_THIS_ROW"
R_LINE_NOT_A_NUMBER = "THE_STATED_LINE_IS_NOT_A_NUMBER_WE_CAN_GRADE_AGAINST"
R_LINE_REQUIRED = "THIS_KIND_NEEDS_A_LINE_AND_THE_ROW_STATES_NONE"
R_ORIENTATION_NOT_ESTABLISHED = (
    "WHICH_LISTED_PARTICIPANT_THIS_CONTRACT_BACKS_IS_NOT_ESTABLISHED")
R_FIXTURE_SIDES_NOT_TWO = "THE_EVENT_DOES_NOT_NAME_TWO_ORDERED_PARTICIPANTS"
R_OVERTIME_NOT_CAPTURED = "THE_VENUES_OVERTIME_RULE_FOR_THIS_CONTRACT_IS_UNREAD"
R_PERIOD_NOT_ESTABLISHED = "THE_CONTRACTS_PERIOD_IS_NOT_ESTABLISHED"
R_QUANTITY_NOT_POSITIVE = "THE_HELD_QUANTITY_IS_NOT_A_POSITIVE_NUMBER"
R_BASIS_NOT_STATED = "THE_PER_UNIT_BASIS_OR_PRICE_IS_NOT_STATED"
R_LEG_FACTS_MISSING = "THE_LEG_IS_MISSING_A_FACT_GRADING_NEEDS"
R_SAME_CONTRACT = "THE_CANDIDATE_IS_THE_HELD_CONTRACT_ITSELF"
R_NETTED_SAME_INSTRUMENT = (
    "THE_CANDIDATE_IS_THE_OPPOSING_SIDE_OF_THE_HELD_INSTRUMENT")
R_CANDIDATE_NOT_PRICED = "THIS_CANDIDATES_OWN_PRICE_WAS_NOT_ESTABLISHED"
R_HELD_SIDE_NOT_STATED = "WHICH_SIDE_OF_THE_INSTRUMENT_IS_HELD_IS_NOT_STATED"
R_HELD_SIDE_NOT_IN_CATALOGUE = (
    "THE_CATALOGUE_HAS_NO_ROW_FOR_THE_SIDE_THE_POSITION_STATES")
R_SIDE_NOT_STATED_ON_ROW = "THE_CATALOGUE_ROW_STATES_NO_SIDE"
R_BOTH_SIDES_CLAIM_ONE_ORIENTATION = (
    "TWO_SIDES_OF_ONE_INSTRUMENT_CLAIM_THE_SAME_ORIENTATION")


# ═════════════════════════════════════════════════════════════════════
# 0 · THE IDENTITY MODEL: A VENUE INSTRUMENT IS NOT A CANDIDATE
# ═════════════════════════════════════════════════════════════════════
#
# WHAT RUN 268 MEASURED, on the production catalogue, 52,950 rows:
#
#   rows per market_slug ................................. exactly 2.000
#   `identifier` distinct values per slug ................. 1  (identifier IS
#                                                            the slug, so it is
#                                                            NOT the side key)
#   slugs where `side_norm` differs between the rows ...... 26,475 of 26,475
#   slugs where `intent` differs between the rows ......... 26,475 of 26,475
#   slugs where `line` or `sports_type` differ ............ 0
#   the whole `intent` vocabulary ......................... ORDER_INTENT_BUY_LONG
#                                                           ORDER_INTENT_BUY_SHORT
#                                                           (26,475 slugs each)
#
# So ONE VENUE INSTRUMENT CARRIES TWO SIDES, and `(market_slug, intent)` names
# one of them. The two sides share the line and the type and differ in which
# outcome token you hold -- for a spread, `signed` is -1.5 on one row and +1.5
# on the other, which is the SAME contract read from opposite ends and the
# whole payout function of the leg.
#
# TWO CONSEQUENCES, AND THEY PULL IN OPPOSITE DIRECTIONS, which is why this
# section exists instead of a rename:
#
#   1. THE HELD LEG MUST BE BOUND TO THE SIDE ACTUALLY HELD. `ROW_SQL` was
#      `WHERE market_slug = $1 LIMIT 1` with no ORDER BY, so orientation came
#      from whichever of the two rows Postgres returned. Half the time that is
#      the wrong end of the contract and the leg's payout is inverted, with no
#      error anywhere.
#
#   2. A CANDIDATE'S IDENTITY MUST CARRY ITS SIDE. Keying by slug alone
#      collapsed the two sides onto one id -- measured: 16,545 candidates
#      across 1,558 future fixtures, which is EVERY future fixture and exactly
#      half the candidate set. Two ranked rows shared one condition_id with
#      different scores, and `best_admitted` resolved that id to whichever
#      entry happened to be last.
#
# AND THE THING NOT TO DO, in the owner's words: "Do not pretend opposite sides
# are separate venue instruments to avoid fixing the identity model." So the
# netting exclusion stays on `market_slug`: the held instrument's other side is
# the held instrument, it nets, and it is never a liquidity opportunity. A
# side-aware CANDIDATE identity and a slug-level NETTING identity are different
# questions and both are answered, separately.

#: The two sides of every PMUS instrument, as the catalogue states them. Not an
#: invented vocabulary: `us_premap.intent` takes exactly these two values and
#: nothing else, one row of each per slug.
SIDE_LONG = "ORDER_INTENT_BUY_LONG"
SIDE_SHORT = "ORDER_INTENT_BUY_SHORT"
SIDES = (SIDE_LONG, SIDE_SHORT)

#: The separator in a candidate identity. `#` cannot occur in a venue slug --
#: every slug is lowercase alphanumerics and hyphens -- so `split_identity` is
#: unambiguous and an identity can never be mistaken for a slug by a consumer
#: that does not know about sides.
IDENTITY_SEP = "#"


def side_of(row) -> str | None:
    """Which side of its instrument this catalogue row is, or None.

    `intent` is the key because run 268 measured it varying on all 26,475
    slugs with exactly two values. `side_norm` also varies on all of them, but
    its values are the participant's name on a moneyline, yes/no on a spread
    and over/under on a total -- three vocabularies -- so it identifies the
    side without NAMING it in a way two market families share.
    """
    intent = _side_token((dict(row) if not isinstance(row, dict) else row)
                         .get("intent"))
    return intent if intent in SIDES else None


def candidate_identity(market_slug, side) -> str:
    """The side-aware identity: `slug#SIDE`.

    THIS IS THE KEY EVERYTHING DOWNSTREAM USES -- quoting, valuation, ranking,
    persistence and the selected-candidate lookup. `venue_slug_of` recovers the
    slug for the parts that address the venue, which is the only place the slug
    alone is the right identifier.
    """
    return "%s%s%s" % (_clean(market_slug), IDENTITY_SEP,
                       _side_token(side))


def split_identity(identity) -> tuple[str, str | None]:
    """(venue slug, side) from an identity. A bare slug yields (slug, None).

    Accepting a bare slug is deliberate: a persisted decision written before
    this change carries one, and reading it back as "slug, side unknown" is
    correct -- that is a fact about the record, not a default to dispatch on.
    """
    # THE TWO HALVES NORMALISE DIFFERENTLY, which is why this does not just
    # `_clean` the whole string: slugs are lowercase and the side tokens are
    # uppercase. Lowercasing the lot turns every side into an unrecognised one.
    text = str(identity or "").strip()
    if IDENTITY_SEP not in text:
        return _clean(text), None
    slug, _, side = text.partition(IDENTITY_SEP)
    side = _side_token(side)
    return _clean(slug), (side if side in SIDES else None)


def venue_slug_of(identity) -> str:
    """What to send to the venue. The slug, never the identity."""
    return split_identity(identity)[0]


#: THE PROVENANCE TABLE. A reviewer should be able to ask "where did this
#: field come from" and get an answer without reading the code, and a field
#: with no source is a refusal rather than a default.
LEG_FIELD_SOURCES = {
    "condition_id": ("us_premap.market_slug -- the VENUE-native id. Never "
                     "markets.slug, which is the global catalogue's id and "
                     "matches zero venue rows",
                     R_NO_CATALOGUE_ROW),
    "fixture_id": ("us_premap.event_slug. It is also the ORDERING AUTHORITY "
                   "for orientation, because the two participant codes appear "
                   "in it in the fixture's listed order",
                   R_NO_EVENT),
    "kind": ("derived from side_norm's class and whether a handicap is "
             "stated -- NOT from `kind`, which is the constant 'side'",
             R_KIND_NOT_DERIVABLE),
    "period": ("the period metadata the entry lane already reads. A contract "
               "whose period is not stated is refused rather than assumed to "
               "be the full game",
               R_PERIOD_NOT_ESTABLISHED),
    "overtime": ("the VENUE'S OWN published settlement prose, read per "
                 "contract by bettor_live_read.read_rules_text and matched "
                 "against bettor_venue_settlement.OVERTIME_PROSE. Absent "
                 "prose is OT_UNKNOWN, which Leg.missing_facts refuses",
                 R_OVERTIME_NOT_CAPTURED),
    "backs": ("us_premap.team_abbr against the ordered participant codes in "
              "event_slug, or side_norm for a totals contract",
              R_ORIENTATION_NOT_ESTABLISHED),
    "line": ("us_premap.line / .signed, parsed from TEXT to a Fraction",
             R_LINE_NOT_A_NUMBER),
    "over_under": ("us_premap.side_norm for a totals contract", None),
    "quantity": ("the funded intent's residual quantity", R_QUANTITY_NOT_POSITIVE),
    "cost_cents_per_unit": ("the funded intent's per-unit basis",
                            R_BASIS_NOT_STATED),
    # ONE CLAUSE, NOT THE DOCUMENT. These two used to name "the same captured
    # venue prose" -- the whole blob in both fields -- which is how a sentence
    # about a drawn fixture established the payout for a fixture that never
    # happened.
    "tie_rule": ("the ONE clause of the captured prose that names a tie and "
                 "states its payout, per bettor_settlement_clauses. No such "
                 "clause leaves the TIE region's payout UNDETERMINED, which "
                 "propagates as UNESTABLISHABLE -- it is never filled in from "
                 "memory, and never from a clause about another outcome",
                 None),
    "void_rule": ("the ONE clause that names a cancellation and states its "
                  "payout. Same treatment, and separately determined: a tie "
                  "clause establishes nothing here", None),
    "settlement_rules": ("all five exceptional outcomes read separately by "
                         "bettor_settlement_clauses.interpret, each with its "
                         "source clause, payout class and refusal", None),
    "settlement_provenance": ("the captured text, its source, retrieval age, "
                              "sha256 content hash and interpretation version, "
                              "so a decision made on this reading stays "
                              "auditable if the venue's text later changes",
                              None),
}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE OVERTIME RULE, WHICH IS THE ONE THAT CANNOT BE PLUMBED
# ═════════════════════════════════════════════════════════════════════
#
# `bettor_venue_settlement.ATTESTED` is deliberately empty, so `rule_for`
# returns None for every family and no FAMILY-LEVEL overtime rule exists.
# That is not an oversight to work around: the module records WHY_EMPTY and
# HOW_TO_ESTABLISH, and a family-level default would be the "supply the
# venue's rules from memory" that `Leg` names as the fabrication.
#
# What DOES exist per contract is the venue's own published prose, which
# `bettor_live_read.read_rules_text` fetches and the entry lane already
# reads. `OVERTIME_PROSE` holds the DECLARED patterns for matching it.
#
# ── THE TRAP IN OVERTIME_PROSE, AND IT IS EASY TO WALK INTO ──────────
#
# Its keys are named `includes` and `excludes`, and they do NOT mean "the
# venue includes overtime" / "the venue excludes it". They mean AGREES WITH
# THE BOOK RULE and CONTRADICTS IT -- `attest` uses them that way, and each
# family carries `book_rule_includes_overtime` to say which way round it is.
#
#   baseball  book_rule_includes_overtime = True
#             so `includes` matched  -> the VENUE includes extra innings
#   soccer    book_rule_includes_overtime = False
#             so `includes` matched  -> the venue is REGULATION ONLY, which
#             is OT_EXCLUDED. The patterns bear this out: soccer's
#             `includes` list is ("90 minutes", "regulation time only",
#             "excludes extra time", ...).
#
# Reading those labels literally would give every soccer leg the OPPOSITE
# overtime treatment, two such legs would share a grading_key they do not
# share, and the pair would be classified as a hedge against a variable
# neither leg grades against. That is the failure this function exists to
# not have, so the mapping is written out and pinned by a test.

R_OT_PROSE_STATES_BOTH = "THE_PROSE_MATCHES_BOTH_AN_INCLUDING_AND_AN_EXCLUDING_PATTERN"
R_OT_PROSE_SILENT = "THE_PROSE_WAS_READ_AND_STATES_NOTHING_ABOUT_OVERTIME"
R_OT_NO_PATTERNS = "NO_OVERTIME_PATTERNS_ARE_DECLARED_FOR_THIS_SPORT_FAMILY"
R_OT_NO_PROSE = "NO_VENUE_SETTLEMENT_PROSE_WAS_CAPTURED_FOR_THIS_CONTRACT"


def overtime_from_venue_prose(*, sport_family, prose) -> dict:
    """The VENUE'S OWN overtime treatment for one contract, or OT_UNKNOWN.

    Returns {"overtime": OT_*, "established": bool, "refusal": str|None, ...}.
    Never raises. OT_UNKNOWN is the only answer when the prose is absent,
    silent, self-contradictory, or the family declares no patterns -- and
    `Leg.missing_facts` turns OT_UNKNOWN into a refusal, so an unread rule
    cannot become a graded leg.

    This asks a DIFFERENT question from `bettor_venue_settlement.attest`.
    `attest` asks whether the venue's rule agrees with the BOOKMAKER's,
    because an entry prices a venue contract off a bookmaker probability. A
    hedge is two VENUE contracts, so the bookmaker is not in the comparison
    at all; what matters is whether the two legs treat overtime the same way
    as each other. So this reports the venue's own rule, derived from the
    same declared patterns, and the book rule appears only as the reference
    point those patterns are labelled against.
    """
    fam = str(sport_family or "")
    text = " ".join(str(prose or "").lower().split())
    out = {"sport_family": fam, "overtime": IS.OT_UNKNOWN,
           "established": False, "refusal": None,
           "prose_read": bool(text), "prose_chars": len(text),
           "matched_agreeing": [], "matched_contradicting": [],
           "book_rule": VS.BOOK_SETTLEMENT.get(fam),
           "labels_are_relative_to_the_book_rule": (
               "OVERTIME_PROSE's `includes`/`excludes` mean AGREES WITH / "
               "CONTRADICTS the book rule, not includes/excludes overtime. "
               "`book_rule_includes_overtime` is what turns one into the "
               "other, and reading the labels literally gives soccer the "
               "opposite treatment"),
           "this_is_the_venues_own_rule_not_a_compatibility_verdict": True}
    pats = VS.OVERTIME_PROSE.get(fam)
    if not pats:
        out["refusal"] = R_OT_NO_PATTERNS
        out["why"] = ("no overtime prose patterns are declared for %r, so no "
                      "reading of this contract's text is reviewable. A "
                      "pattern invented here would be a claim about the "
                      "sport made at the point of use" % (fam,))
        return out
    if not text:
        out["refusal"] = R_OT_NO_PROSE
        out["why"] = ("no venue settlement prose was captured for this "
                      "contract. Absent text is the ABSENCE of a rule, never "
                      "agreement with one -- and the overtime treatment is "
                      "part of the grading key, so two legs cannot be "
                      "matched without it")
        return out

    agreeing = [p for p in (pats.get("includes") or ()) if re.search(p, text)]
    contradicting = [p for p in (pats.get("excludes") or ())
                     if re.search(p, text)]
    out["matched_agreeing"] = agreeing
    out["matched_contradicting"] = contradicting
    book_includes = bool(pats.get("book_rule_includes_overtime"))

    if agreeing and contradicting:
        out["refusal"] = R_OT_PROSE_STATES_BOTH
        out["why"] = ("the contract's own prose matches both an agreeing and "
                      "a contradicting pattern, so it does not state one "
                      "rule. Two readings of the same text establish nothing")
        return out
    if not agreeing and not contradicting:
        out["refusal"] = R_OT_PROSE_SILENT
        out["why"] = ("the venue publishes prose for this contract and it "
                      "says nothing about the terminal case. The text was "
                      "READ, so this is a fact about the prose rather than "
                      "about our records -- and it is still not a rule")
        return out

    # ── THE MAPPING. `agreeing` means the venue matches the book rule. ──
    agrees_with_book = bool(agreeing)
    venue_includes_ot = book_includes if agrees_with_book else (
        not book_includes)
    out.update(
        overtime=(IS.OT_INCLUDED if venue_includes_ot else IS.OT_EXCLUDED),
        established=True,
        agrees_with_book_rule=agrees_with_book,
        why=("the contract's own prose matches %d declared %s pattern(s); "
             "the book rule for %s %s overtime, so the venue %s it"
             % (len(agreeing or contradicting),
                "agreeing" if agrees_with_book else "contradicting", fam,
                "includes" if book_includes else "excludes",
                "includes" if venue_includes_ot else "excludes")))
    return out


# ═════════════════════════════════════════════════════════════════════
# 2 · THE KIND AND THE PERIOD, FROM sports_type AND NOTHING ELSE
# ═════════════════════════════════════════════════════════════════════
#
# `kind` is the constant 'side'. `line` is the GAME START MINUTE on a
# moneyline row -- 00, 30, 45, 05 and so on, every value two digits in
# 00..59, with the baseball rows on '00' starting at 9:00 AM UTC. `side_norm`
# is the team's own name on a moneyline and yes/no on a spread. NOT ONE of
# those three columns separates a moneyline from a spread, and each of them
# looks as though it does. Two successive versions of this function read one
# of them and were refuted by the live catalogue.
#
# `sports_type` names both the kind and the period, explicitly:
#
#     baseball_team_full_game_winner    moneyline, full game
#     football_team_first_half_spread   spread,    first half
#     tennis_match_winner               moneyline, full match
#
# ── WHY THE ALLOWLIST IS NARROW, AND THAT IS THE POINT ───────────────
#
# `Leg` grades exactly three variables -- signed MARGIN, combined TOTAL,
# three-way WIN3 -- over six named periods. 215 sports_types exist and most
# are neither: `football_player_receiving_yards`, `soccer_game_total_corners`,
# `esports_map_rounds_handicap_1` and `baseball_player_hits` are real markets
# graded against variables this module has no representation for.
#
# Mapping a corners total onto VAR_TOTAL would be the worst available error
# here, and it is an easy one: both are "a total", so the pair would share a
# `grading_key()` -- fixture, period, VAR_TOTAL, overtime -- while one settles
# on corners and the other on points. `discover` would then classify two
# unrelated contracts as a middle and report a guaranteed minimum payout that
# does not exist. So the allowlist admits a type only when the VARIABLE ITSELF
# is one of the three, and everything else is refused by name.
#
# A hockey first period is refused for the same reason from the other
# direction: the period vocabulary has FULL_GAME, FIRST_HALF, SECOND_HALF,
# FIRST_QUARTER, MAP_OR_GAME and SERIES, and a hockey period is none of them.
# Calling it FIRST_QUARTER would match it against a basketball quarter.

#: sports_type SUFFIX -> (kind, period). Matched on the suffix because the
#: prefix is the sport family, which `bettor_venue_realism` owns. Longest
#: suffix wins, so `_game_first_half_total_points` is never read as
#: `_game_total_points`.
#:
#: EVERY ENTRY IS A CLAIM ABOUT A SETTLEMENT VARIABLE and is admitted only
#: because the type's own words name it. `_winner` on a two-participant
#: fixture is the sign of the margin; `_spread` is the margin against a line;
#: `_total_points` is the combined score. Nothing is admitted because it
#: sounds close to one of those.
GRADED_SUFFIXES = (
    ("_game_first_half_total_points", (IS.KIND_TOTAL, IS.PERIOD_H1)),
    ("_game_second_half_total_points", (IS.KIND_TOTAL, IS.PERIOD_H2)),
    ("_team_first_half_spread", (IS.KIND_SPREAD, IS.PERIOD_H1)),
    ("_team_second_half_spread", (IS.KIND_SPREAD, IS.PERIOD_H2)),
    ("_team_full_game_spread", (IS.KIND_SPREAD, IS.PERIOD_FULL)),
    ("_team_first_half_winner", (IS.KIND_MONEYLINE, IS.PERIOD_H1)),
    ("_team_full_game_winner", (IS.KIND_MONEYLINE, IS.PERIOD_FULL)),
    # ADDED FROM MEASURED COVERAGE, NOT FROM MEMORY. Run 265 showed
    # `soccer_team_full_time_winner` carrying 1,338 rows over 223 events and
    # excluded -- the largest genuine fixture-level winner market the first
    # list left out, and excluded only because the venue spells it `full_time`
    # where the American sports spell it `full_game`. The simulated twin
    # `efootball_team_full_time_winner` (2,934 rows) matches this suffix too
    # and is refused upstream by `bettor_venue_realism`, which is where that
    # refusal belongs -- not duplicated here as a second, divergent list.
    ("_team_full_time_winner", (IS.KIND_MONEYLINE, IS.PERIOD_FULL)),
    ("_game_total_points", (IS.KIND_TOTAL, IS.PERIOD_FULL)),
    ("_match_winner", (IS.KIND_MONEYLINE, IS.PERIOD_FULL)),
    ("_fight_winner", (IS.KIND_MONEYLINE, IS.PERIOD_FULL)),
)

#: Types whose OWN WORDS state the overtime treatment, which is stronger than
#: any prose read. `hockey_team_regulation_winner` says regulation, so the
#: contract excludes overtime by its own name. Kept separate from
#: GRADED_SUFFIXES because it supplies a third fact, and kept SHORT because a
#: guess here would defeat the prose read rather than supplement it.
SUFFIX_STATES_OVERTIME = {
    "_team_regulation_winner": IS.OT_EXCLUDED,
}

R_TYPE_NOT_A_GRADED_VARIABLE = (
    "THIS_MARKET_IS_GRADED_AGAINST_A_VARIABLE_THIS_MODULE_DOES_NOT_REPRESENT")
R_NO_SPORTS_TYPE = "THE_CATALOGUE_ROW_STATES_NO_SPORTS_TYPE"

#: Never a handicap. Recorded as a constant so the next reader does not have
#: to rediscover it from a column that looks numeric and useful.
LINE_IS_NOT_A_HANDICAP_ON_A_MONEYLINE = (
    "us_premap.line on a moneyline row is the GAME START MINUTE: every value "
    "in the catalogue is two digits in 00..59, and the baseball rows carrying "
    "'00' start at 9:00 AM UTC. Reading it as a handicap builds every "
    "moneyline as a spread at line 20")

SIDE_CLASS_TOTAL = ("over", "under")


def _clean(value) -> str:
    return str(value or "").strip().lower()


def _side_token(value) -> str:
    """A side, normalised WITHOUT lowercasing.

    `_clean` lowercases, which is right for slugs and wrong for these: the
    catalogue's own values are ORDER_INTENT_BUY_LONG and
    ORDER_INTENT_BUY_SHORT, uppercase, and `_clean` turned every one of them
    into something not in SIDES -- so every held leg refused. Caught by the
    integration suite immediately; it is here as its own function so the two
    normalisations cannot be confused again.
    """
    return str(value or "").strip().upper()


def parse_line(text) -> dict:
    """A Fraction from the catalogue's TEXT line, or a named refusal.

    `line` is a text column. A cast on the decision path that raises is a
    provider format change taking out a cycle, so this returns a refusal.
    It is called ONLY for kinds whose type says they carry a handicap; on a
    moneyline the same column holds the start minute.
    """
    raw = str(text or "").strip()
    out = {"stated": bool(raw), "raw": raw, "line": None, "refusal": None,
           "half_integer": None}
    if not raw:
        return out
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
        out["refusal"] = R_LINE_NOT_A_NUMBER
        out["why"] = ("the catalogue states the line as %r, which is not a "
                      "plain number. A line we cannot place on the margin "
                      "axis cannot partition the outcome space" % (raw,))
        return out
    frac = Fraction(raw)
    out["line"] = frac
    # A HALF-INTEGER LINE CANNOT PUSH; an integer one can, and
    # `Leg.missing_facts` refuses an integer line whose push rule was not
    # captured. Reported so the caller can see which case it is in.
    out["half_integer"] = (frac.denominator == 2)
    return out


def derive_kind(row) -> dict:
    """Which graded market kind and period this row is, or a refusal.

    Pure; takes a mapping of the `us_premap` columns. Never raises.

    THE SIGNED HANDICAP, NOT THE LINE, is what a spread's line is read from.
    `signed` carries '-10.5' / '+10.5' oriented to the row's own team, which
    is what `Leg.line` needs (the handicap expressed against team A's
    margin); `line` carries the bare magnitude on a spread and the start
    minute on a moneyline, so it is not read here at all.
    """
    r = dict(row or {})
    st = _clean(r.get("sports_type"))
    out = {"kind": None, "period": None, "over_under": None, "line": None,
           "signed_line": None, "needs_orientation_to_become_leg_line": False,
           "overtime_from_type": None, "refusal": None, "sports_type": st,
           "matched_suffix": None,
           "derived_from": ("sports_type, which names both the kind and the "
                            "period. `kind` is the constant 'side', and "
                            + LINE_IS_NOT_A_HANDICAP_ON_A_MONEYLINE)}
    if not st:
        out["refusal"] = R_NO_SPORTS_TYPE
        out["why"] = ("the row states no sports_type, so neither the graded "
                      "variable nor the period is established. A default of "
                      "full-game moneyline would grade the leg against a "
                      "variable nobody read")
        return out

    for suffix, ot in SUFFIX_STATES_OVERTIME.items():
        if st.endswith(suffix):
            out["overtime_from_type"] = ot

    hit = None
    for suffix, (kind, period) in GRADED_SUFFIXES:
        if st.endswith(suffix) and (hit is None or len(suffix) > len(hit[0])):
            hit = (suffix, kind, period)
    if hit is None:
        out["refusal"] = R_TYPE_NOT_A_GRADED_VARIABLE
        out["why"] = ("%r is not one of the market types this module can "
                      "grade. `Leg` represents a signed margin, a combined "
                      "total and a three-way result; a player prop, a corners "
                      "total or a rounds handicap settles on something else, "
                      "and mapping one onto VAR_TOTAL would give two legs a "
                      "shared grading key for different underlying counts"
                      % (st,))
        return out
    suffix, kind, period = hit
    out.update(kind=kind, period=period, matched_suffix=suffix)

    if kind == IS.KIND_TOTAL:
        side = _clean(r.get("side_norm"))
        if side not in SIDE_CLASS_TOTAL:
            out.update(kind=None, period=None,
                       refusal=R_KIND_NOT_DERIVABLE,
                       why=("the type names a total and side_norm is %r, "
                            "which states neither OVER nor UNDER. A total "
                            "whose direction is unstated has no payout "
                            "function" % (side or None,)))
            return out
        out["over_under"] = side.upper()
        lr = parse_line(r.get("line"))
        if lr["refusal"] or lr["line"] is None:
            out.update(kind=None, period=None,
                       refusal=(lr["refusal"] or R_LINE_REQUIRED),
                       line_read=lr,
                       why=(lr.get("why") or
                            "the type names a total and no line is stated"))
            return out
        out["line"] = lr["line"]
        out["line_read"] = lr
        out["why"] = ("sports_type names a total over %s; side_norm states "
                      "the direction and the line is its number" % (period,))
        return out

    if kind == IS.KIND_SPREAD:
        # THE SIGNED HANDICAP, ORIENTED TO THIS ROW'S OWN TEAM. `signed` is
        # '-10.5' on the favourite's row and '+10.5' on the other, which is
        # exactly what a per-leg handicap is. `line` is the bare magnitude and
        # would lose the sign -- the leak-hunt note in premap records a whale
        # taking +3.5 being matched to the venue's -3.5 side, the opposite
        # bet, on every spread, for precisely that reason.
        #
        # IT IS NOT YET `Leg.line`. See `line_against_a` below: Leg.line is
        # always expressed against TEAM A's margin whichever side the leg
        # backs, so the B-side row's +10.5 has to become -10.5. That
        # conversion needs the orientation, which this function does not have,
        # so what comes back here is `signed_line` and the caller converts.
        sr = parse_line(r.get("signed"))
        if sr["refusal"] or sr["line"] is None:
            out.update(kind=None, period=None,
                       refusal=(sr["refusal"] or R_LINE_REQUIRED),
                       line_read=sr,
                       why=(sr.get("why") or
                            ("the type names a spread and the row states no "
                             "signed handicap. The unsigned `line` column "
                             "carries the magnitude only, and a handicap "
                             "without its sign is the opposite bet half the "
                             "time")))
            return out
        out["signed_line"] = sr["line"]
        out["line_read"] = sr
        out["needs_orientation_to_become_leg_line"] = True
        out["why"] = ("sports_type names a spread over %s; the handicap is "
                      "the row's own SIGNED value, not the unsigned "
                      "magnitude, and it still has to be expressed against "
                      "team A's margin" % (period,))
        return out

    # MONEYLINE. No line is read: on these rows the column is the start
    # minute. A moneyline's payout turns on the sign of the margin alone.
    out["why"] = ("sports_type names a %s winner over %s. No line is read: "
                  "%s" % (st.split("_")[0], period,
                          LINE_IS_NOT_A_HANDICAP_ON_A_MONEYLINE))
    return out


# ═════════════════════════════════════════════════════════════════════
# 3 · ORIENTATION: WHICH LISTED PARTICIPANT THE CONTRACT BACKS
# ═════════════════════════════════════════════════════════════════════
#
# `backs` is "A" or "B", and A is the fixture's FIRST listed participant --
# the same A that VAR_MARGIN is signed towards. So orientation needs an
# ORDERING, and `side_norm` supplies none: it is the team's own name on a
# moneyline and 'yes'/'no' on a spread, and neither says which of the two
# listed participants comes first.
#
# THE ORDERING AUTHORITY IS `event_slug`, and run 264 read it directly:
#
#     event_slug  npb-clm-nhf-2026-10-01
#     title       Chiba Lotte Marines vs. Nippon Ham Fighters
#     rows        team_abbr 'clm' (BUY_LONG) and team_abbr 'nhf' (BUY_SHORT)
#
# league, then the two participant codes in the order the title lists them,
# then the date. `team_abbr` on each row names which of the two that row
# backs, so orientation is an EQUALITY between a code the row states and one
# of two codes the slug states -- not a reading of prose, and not a name
# match. Note what is NOT used: the intent. BUY_LONG happened to sit on the
# first-listed team in both fixtures read, and two examples are not a rule;
# the equality needs no such assumption.
#
# It refuses rather than guessing when the slug does not yield exactly two
# codes, when team_abbr is absent, or when the code is neither of them. A
# guessed orientation inverts the payout function, which is the most expensive
# single error available here: it turns a hedge into a doubled position.

_SLUG_TOKEN = re.compile(r"[a-z0-9]+")
#: A token that is only digits is part of the trailing date. Stripped from the
#: END only -- a participant code sitting mid-slug is never dropped.
_ALL_DIGITS = re.compile(r"\d+")


def fixture_participants(event_slug) -> dict:
    """The fixture's two participant codes IN LISTED ORDER, or a refusal.

    `npb-clm-nhf-2026-10-01` -> ("clm", "nhf"): the trailing date tokens are
    dropped, then the leading league token, and exactly two must remain.
    NOTE that `event_slug` carries NO family prefix -- the `aec-`/`asc-` part
    is on `market_slug` -- so nothing is stripped for it.
    """
    raw = str(event_slug or "").strip().lower()
    out = {"event_slug": raw, "participants": None, "refusal": None,
           "tokens": [], "league": None}
    if not raw:
        out["refusal"] = R_NO_EVENT
        out["why"] = ("the catalogue row names no event, so there is no "
                      "fixture and no listed order to orient against")
        return out
    toks = _SLUG_TOKEN.findall(raw)
    # The trailing ISO date arrives as three all-digit tokens once the hyphens
    # are split. Dropped from the end while they are all digits, which leaves
    # an alphanumeric participant code untouched.
    while toks and _ALL_DIGITS.fullmatch(toks[-1]):
        toks.pop()
    # THE LEAGUE TOKEN, dropped only when THREE remain -- so a slug that is
    # already two tokens is never reduced to one, and a slug of four is
    # refused rather than trimmed until it fits.
    if len(toks) == 3:
        out["league"] = toks[0]
        toks = toks[1:]
    out["tokens"] = list(toks)
    if len(toks) != 2 or toks[0] == toks[1]:
        out["refusal"] = R_FIXTURE_SIDES_NOT_TWO
        out["why"] = ("the event slug yields %d participant token(s) %r, not "
                      "two distinct ones, so the fixture has no established "
                      "listed order and `backs` cannot name A or B. Trimming "
                      "further until two remained would invent the order"
                      % (len(toks), toks))
        return out
    out["participants"] = (toks[0], toks[1])
    out["why"] = ("participant codes in the order the slug lists them, which "
                  "run 264 confirmed is the order the event title lists them. "
                  "A is the first, which is the A that VAR_MARGIN is signed "
                  "towards")
    return out


def orientation_of(row, *, participants=None) -> dict:
    """"A" or "B" for this row's contract, or a refusal. Never a guess."""
    r = dict(row or {})
    out = {"backs": None, "refusal": None, "team_abbr": None,
           "participants": None}
    pr = (dict(participants) if isinstance(participants, dict)
          else fixture_participants(r.get("event_slug")))
    out["participants"] = pr.get("participants")
    if pr.get("refusal"):
        out.update(refusal=pr["refusal"], why=pr.get("why"))
        return out
    a, b = pr["participants"]
    abbr = _clean(r.get("team_abbr"))
    out["team_abbr"] = abbr or None
    if not abbr:
        out["refusal"] = R_ORIENTATION_NOT_ESTABLISHED
        out["why"] = ("the row states no team code. side_norm is %r, and "
                      "neither a 'yes' nor a team's own name establishes "
                      "WHICH of the fixture's two listed participants comes "
                      "first" % (_clean(r.get("side_norm")) or None,))
        return out
    if abbr == a:
        out.update(backs="A", why=("the row backs %r, which the slug lists "
                                   "first" % (abbr,)))
        return out
    if abbr == b:
        out.update(backs="B", why=("the row backs %r, which the slug lists "
                                   "second" % (abbr,)))
        return out
    out["refusal"] = R_ORIENTATION_NOT_ESTABLISHED
    out["why"] = ("the row backs %r and the fixture lists %r and %r. A code "
                  "matching neither participant is not an orientation to be "
                  "resolved by preferring one; it is a disagreement between "
                  "the row and its own event" % (abbr, a, b))
    return out


# ═════════════════════════════════════════════════════════════════════
# 4 · THE SPREAD'S LINE MUST BE EXPRESSED AGAINST TEAM A'S MARGIN
# ═════════════════════════════════════════════════════════════════════
#
# THE DEFECT THIS EXISTS TO NOT HAVE, CAUGHT BY EXERCISING THE FUNCTION
# AGAINST THE REAL ROWS RATHER THAN BY READING IT. `derive_kind` returned the
# row's own signed handicap as `Leg.line`, and on the B-side row that is the
# WRONG SIGN. `Leg` states the convention in its own comment:
#
#     Bears -4.5 backing Bears is line=-4.5 backs="A"; Panthers +4.5 backing
#     Panthers is the SAME contract from B's side, so line=-4.5 backs="B".
#
# `line` is ALWAYS the handicap against TEAM A's margin, whichever side the
# leg backs. The two rows of one venue spread are mirror images -- run 264
# shows `asc-nfl-lar-den-...-1h-neg-10pt5` carrying lar '-10.5' and den
# '+10.5' -- so the B-side row's value has to be NEGATED to recover A's.
#
# Left unfixed, the den leg would have carried line=+10.5 and
# `_leg_payout_cents` would have graded it as "A wins by more than -10.5",
# i.e. A losing by up to 10 still pays. That inverts the payout function of
# every B-side spread, and a pair built from one would be classified as a
# middle that cannot lose while actually being a doubled position on one
# side. It is the single most expensive error available in this module and it
# was not visible by inspection; it appeared the moment the real den row went
# through.

R_LINE_NEEDS_ORIENTATION = (
    "A_SPREADS_LINE_CANNOT_BE_ORIENTED_WITHOUT_KNOWING_WHICH_SIDE_IT_BACKS")


def line_against_a(*, signed_line, backs) -> dict:
    """The handicap expressed against TEAM A's margin, or a refusal.

    `signed_line` is the row's own handicap, oriented to the team THAT ROW
    backs. Returns it unchanged when the row backs A and negated when it
    backs B, because the two rows of one spread are mirror images and
    `Leg.line` is defined against A.
    """
    out = {"line": None, "refusal": None, "signed_line": signed_line,
           "backs": backs,
           "convention": ("Leg.line is the handicap against TEAM A's margin "
                          "whichever side the leg backs, so a B-side row's "
                          "value is negated rather than carried")}
    if signed_line is None:
        out["refusal"] = R_LINE_REQUIRED
        out["why"] = "no signed handicap was read, so there is none to orient"
        return out
    if backs == "A":
        out.update(line=Fraction(signed_line),
                   why="the row backs A, so its handicap already is A's")
        return out
    if backs == "B":
        out.update(line=-Fraction(signed_line),
                   why=("the row backs B, so its %+s is A's %+s -- the same "
                        "contract read from the other side"
                        % (signed_line, -Fraction(signed_line))))
        return out
    out["refusal"] = R_LINE_NEEDS_ORIENTATION
    out["why"] = ("orientation is %r, so it is not established whether this "
                  "handicap is A's or the negation of A's. The two differ by "
                  "the whole payout function of the leg" % (backs,))
    return out


# ═════════════════════════════════════════════════════════════════════
# 5 · THE BUILDER: A Leg, OR A REFUSAL NAMING THE FACT THAT WAS MISSING
# ═════════════════════════════════════════════════════════════════════
#
# Everything above is a reader for ONE fact. This is where they combine into
# the object `discover` consumes, and it is the part that was missing when the
# previous batch reported this item done: parsing helpers existed, no `Leg` was
# ever constructed, and `funded_pair_inputs` still returned `held_leg=None`.
#
# PROVENANCE TRAVELS WITH THE LEG. `built_from` records, per field, which
# source supplied it -- catalogue row, settlement prose, the funded intent, the
# venue ladder -- so a decision that reaches a ledger can be audited back to
# the reads that produced it without re-running anything. A field with no
# source is not defaulted; the build refuses and names it.

#: Every fact a built leg carries a source for. Checked by a test against
#: `Leg`'s own dataclass fields so a new required field cannot be added
#: upstream and silently arrive unsourced.
PROVENANCE_KEYS = ("condition_id", "fixture_id", "kind", "period", "overtime",
                   "backs", "line", "over_under", "quantity",
                   "cost_cents_per_unit", "tie_rule", "void_rule",
                   "settlement_rules", "settlement_provenance")


class LegRefused(Exception):
    """A leg that could not be built. Carries the refusal and the field."""

    def __init__(self, refusal, why, field=None, detail=None):
        super().__init__(why)
        self.refusal = refusal
        self.why = why
        self.field = field
        self.detail = detail

    def as_dict(self) -> dict:
        return {"ok": False, "refusal": self.refusal, "why": self.why,
                "field": self.field, "detail": self.detail}


def _cents_per_unit(value, field):
    """An integer cent basis, or refuse. `Leg` costs are exact in cents."""
    if value is None:
        raise LegRefused(R_BASIS_NOT_STATED,
                         "%s is not stated, so the leg has no cost basis and "
                         "no structure built on it has a cost" % field, field)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise LegRefused(R_BASIS_NOT_STATED,
                         "%s is %r, which is not a number" % (field, value),
                         field, value)
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:
        raise LegRefused(R_BASIS_NOT_STATED,
                         "%s is %r; a per-unit basis must be finite and above "
                         "zero" % (field, value), field, value)
    # A venue price is quoted in dollars per contract on a $1 payout, so cents
    # are the price times one hundred. Rounded to the cent because that is the
    # unit `Leg` states and `_leg_payout_cents` compares against.
    return int(round(v * 100)) if v <= 1.5 else int(round(v))


def build_leg(*, row, quantity, cost_per_unit, prose=None, sport_family=None,
              prose_source=None, evidence_age_s=None) -> dict:
    """ONE `bettor_indirect_structures.Leg` from real reads, or a refusal.

    `row`            a `us_premap` row as a mapping
    `quantity`       contracts held or to be acquired, a positive integer
    `cost_per_unit`  the per-contract price or basis, in dollars
    `prose`          the venue's own captured settlement text for THIS
                     contract, from `bettor_live_read.read_rules_text`
    `sport_family`   the family the overtime patterns are declared for

    Returns {"ok": True, "leg": Leg, "built_from": {...}, ...} or
    {"ok": False, "refusal": ..., "field": ..., "why": ...}. Never raises: a
    refusal on the decision path must be a value, not an exception.

    IT DOES NOT JUDGE THE LEG'S VALUE. It answers only "are all the facts
    grading needs stated", and `Leg.missing_facts()` is consulted as the final
    word rather than re-implemented here -- if that class gains a requirement,
    this builder starts refusing rather than starting to lie.
    """
    prov: dict = {}
    try:
        r = dict(row or {})
        slug = _clean(r.get("market_slug"))
        if not slug:
            raise LegRefused(R_NO_CATALOGUE_ROW,
                             "the row names no market_slug, so the contract has "
                             "no venue-native identity", "condition_id")
        # ── THE IDENTITY IS SIDE-AWARE, THE VENUE ADDRESS IS NOT ─────
        #
        # One venue instrument carries two sides with opposite payout
        # functions, so the SLUG is not an identity for a leg. `condition_id`
        # becomes `slug#SIDE`; `venue_slug` is carried beside it for the parts
        # that address the venue, which is the only place the bare slug is the
        # right identifier.
        side = side_of(r)
        if side is None:
            raise LegRefused(
                R_SIDE_NOT_STATED_ON_ROW,
                "the row states intent=%r, not one of %s. Run 268 measured "
                "every one of the catalogue's 26,475 slugs carrying exactly "
                "these two, one row each, so a row without one is not a side "
                "of anything" % (_side_token(r.get("intent")), SIDES),
                "condition_id")
        identity = candidate_identity(slug, side)
        prov["condition_id"] = (
            "us_premap.market_slug + us_premap.intent as %r -- the VENUE-native "
            "slug is the netting identity and is NOT the leg's identity, "
            "because the same slug carries a second side with the opposite "
            "payout function" % identity)
        prov["side"] = ("us_premap.intent, one of the exactly two values run "
                        "268 measured across all 26,475 slugs")

        fx = fixture_participants(r.get("event_slug"))
        if fx["refusal"]:
            raise LegRefused(fx["refusal"], fx["why"], "fixture_id", fx)
        prov["fixture_id"] = "us_premap.event_slug %r" % (fx["event_slug"],)

        kd = derive_kind(r)
        if kd["refusal"]:
            raise LegRefused(kd["refusal"], kd["why"], "kind", kd)
        prov["kind"] = "us_premap.sports_type via %s" % (kd["matched_suffix"],)
        prov["period"] = prov["kind"]

        # ── ORIENTATION, WHICH A TOTAL DOES NOT HAVE ─────────────────
        backs = None
        if kd["kind"] != IS.KIND_TOTAL:
            ori = orientation_of(r, participants=fx)
            if ori["refusal"]:
                raise LegRefused(ori["refusal"], ori["why"], "backs", ori)
            backs = ori["backs"]
            prov["backs"] = ("us_premap.team_abbr %r against the ordered "
                            "participants %r" % (ori["team_abbr"],
                                                ori["participants"]))
        else:
            prov["backs"] = ("not applicable: a total has no orientation, it "
                            "has a direction")

        # ── THE LINE, AGAINST TEAM A'S MARGIN ────────────────────────
        line = None
        if kd["kind"] == IS.KIND_SPREAD:
            la = line_against_a(signed_line=kd["signed_line"], backs=backs)
            if la["refusal"]:
                raise LegRefused(la["refusal"], la["why"], "line", la)
            line = la["line"]
            prov["line"] = ("us_premap.signed %s, expressed against team A's "
                           "margin (%s)" % (kd["signed_line"], line))
        elif kd["kind"] == IS.KIND_TOTAL:
            line = kd["line"]
            prov["line"] = "us_premap.line %s" % (line,)
        else:
            prov["line"] = ("not read: on a moneyline row that column is the "
                           "game start minute")
        prov["over_under"] = (("us_premap.side_norm %r" % kd["over_under"])
                              if kd["over_under"] else "not applicable")

        # ── THE OVERTIME RULE: THE TYPE FIRST, THEN THE PROSE ────────
        #
        # A type that states its own rule (`_team_regulation_winner`) is
        # stronger than a prose match, because it is the venue naming the rule
        # in the instrument's own identifier. Otherwise the prose is read.
        fam = _clean(sport_family) or _clean(
            str(r.get("sports_type") or "").split("_")[0])
        if kd.get("overtime_from_type"):
            overtime = kd["overtime_from_type"]
            prov["overtime"] = ("us_premap.sports_type states it in the "
                               "instrument's own name")
            ot_read = {"overtime": overtime, "established": True,
                       "source": "SPORTS_TYPE"}
        else:
            ot_read = overtime_from_venue_prose(sport_family=fam, prose=prose)
            overtime = ot_read["overtime"]
            prov["overtime"] = ("the venue's own published settlement prose "
                               "for this contract via %s"
                               % (prose_source or "read_rules_text",))
        if overtime == IS.OT_UNKNOWN:
            raise LegRefused(
                R_OVERTIME_NOT_CAPTURED,
                ("the overtime treatment is not established (%s), and it is "
                 "part of the grading key: two legs that may or may not count "
                 "overtime are not graded against one variable. %s"
                 % (ot_read.get("refusal"), ot_read.get("why") or "")),
                "overtime", ot_read)

        # ── QUANTITY AND BASIS, FROM THE INTENT, NOT THE CATALOGUE ───
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            raise LegRefused(R_QUANTITY_NOT_POSITIVE,
                             "quantity is %r, which is not a whole number of "
                             "contracts" % (quantity,), "quantity")
        if qty <= 0:
            raise LegRefused(R_QUANTITY_NOT_POSITIVE,
                             "quantity is %r; a leg of no contracts has no "
                             "payout to classify" % (qty,), "quantity")
        prov["quantity"] = "the funded intent's residual quantity"
        cents = _cents_per_unit(cost_per_unit, "cost_per_unit")
        prov["cost_cents_per_unit"] = ("the funded intent's per-unit basis, or "
                                      "the candidate's own quoted price")

        # ── THE SETTLEMENT PROSE, READ ONE OUTCOME AT A TIME ─────────
        #
        # THE DEFECT THIS REPLACES, in the owner's words: "build_leg() passes
        # the entire captured prose into both tie_rule and void_rule.
        # _leg_payout_cents() interprets '50-50' in void_rule as a 50-cent
        # cancellation payout." Reproduced before this change: the text
        #
        #     "...includes any extra innings played. A tie resolves 50-50."
        #
        # established a 50-cent payout for a fixture that NEVER HAPPENED. The
        # prose says nothing whatever about cancellation. One blob in two
        # fields meant one sentence answered every question asked of it.
        #
        # So the prose is now READ PER OUTCOME, and each field receives only
        # the clause that establishes ITS outcome -- or None. Absence still is
        # not a build refusal the way OT_UNKNOWN is: it leaves that region
        # UNDETERMINED, which propagates as UNESTABLISHABLE rather than
        # fabricating a payout. What has changed is that "the venue said
        # nothing about this" is now distinguishable from "the venue said
        # this", per outcome, with the sentence attached.
        captured = bool(str(prose or "").strip())
        read = SETTLE.interpret(prose, source=prose_source,
                                retrieved_at=(None if evidence_age_s is None
                                              else "age_s=%s" % evidence_age_s))
        tie_rec = read["rules"][SETTLE.TIE]
        void_rec = read["rules"][SETTLE.CANCELLED]

        def _clause_prov(outcome, rec):
            if rec["established"]:
                return ("the ONE clause of the venue's captured prose that "
                        "names %s and states its payout: %r"
                        % (outcome, rec["clause"]))
            return ("NOT ESTABLISHED (%s) -- the %s region stays undetermined. "
                    "A payout stated for a different outcome is not evidence "
                    "here, which is the defect this replaces"
                    % (rec["refusal"], outcome))

        prov["tie_rule"] = _clause_prov(SETTLE.TIE, tie_rec)
        prov["void_rule"] = _clause_prov(SETTLE.CANCELLED, void_rec)
        prov["settlement_rules"] = (
            "bettor_settlement_clauses.interpret: five outcomes read "
            "separately, each carrying its source clause. Established here: "
            "%s" % (read["established"] or "none"))
        prov["settlement_provenance"] = (
            "raw text, source, retrieval age, sha256 %s, interpretation %s"
            % (read["provenance"]["content_sha256"][:16], SETTLE.VERSION))

        leg = IS.Leg(
            condition_id=identity, fixture_id=fx["event_slug"], kind=kd["kind"],
            period=kd["period"], overtime=overtime, backs=backs, line=line,
            over_under=kd["over_under"], quantity=qty,
            cost_cents_per_unit=cents,
            # ONLY the establishing clause, never the document.
            tie_rule=tie_rec["clause"] if tie_rec["established"] else None,
            void_rule=void_rec["clause"] if void_rec["established"] else None,
            settlement_text_captured=captured,
            settlement_rules=read["rules"],
            settlement_provenance=read["provenance"])
    except LegRefused as exc:
        return dict(exc.as_dict(), built_from=prov)

    # ── THE CLASS ITSELF IS THE FINAL WORD ───────────────────────────
    gaps = leg.missing_facts()
    if gaps:
        return {"ok": False, "refusal": R_LEG_FACTS_MISSING,
                "why": ("the leg is built and still missing a fact grading "
                        "needs: %s" % "; ".join(gaps)),
                "field": None, "missing_facts": gaps, "built_from": prov,
                "checked_by": "bettor_indirect_structures.Leg.missing_facts"}
    return {"ok": True, "refusal": None, "leg": leg, "built_from": prov,
            "grading_key": leg.grading_key(),
            # THE THREE IDENTIFIERS, KEPT APART ON PURPOSE. `candidate_id` keys
            # quoting, valuation, ranking and persistence; `venue_slug` is what
            # an order addresses; `side` is which outcome token of it.
            "candidate_id": identity,
            "venue_slug": slug,
            "side": side,
            "settlement_text_captured": captured,
            "evidence_age_s": evidence_age_s,
            "why": ("every fact this leg's kind needs is stated and sourced, "
                    "and Leg.missing_facts agrees")}


# ═════════════════════════════════════════════════════════════════════
# 6 · THE PRODUCTION IMPORTERS
# ═════════════════════════════════════════════════════════════════════

#: ONE CATALOGUE ROW, BY SLUG **AND SIDE**.
#:
#: THE DEFECT THIS REPLACES. This was `WHERE market_slug = $1 LIMIT 1` with no
#: ORDER BY. Run 268 measured exactly 2.000 rows per market_slug, so the row
#: that came back -- and with it the leg's orientation, and with that its whole
#: payout function -- was whichever one Postgres happened to return first. On a
#: spread the two rows carry `signed` -1.5 and +1.5: binding to the wrong one
#: inverts the payout, silently.
#:
#: Run 269 established `(market_slug, intent)` is EXACTLY unique over all
#: 52,950 rows -- 52,950 distinct pairs, zero duplicates -- so this is a key
#: and not a heuristic. `LIMIT 1` stays as a belt-and-braces guard, not as the
#: thing making the answer single.
ROW_SQL = """
    SELECT market_slug, event_slug, event_title, question, sports_type,
           side_norm, team_abbr, team_name, line, signed, intent, kind,
           game_start, updated_at
      FROM us_premap
     WHERE market_slug = $1
       AND intent = $2
     LIMIT 1
"""

#: Both sides of one slug, for reporting what the catalogue actually offers
#: when the side asked for is not there.
ROW_SIDES_SQL = """
    SELECT intent, side_norm, team_abbr
      FROM us_premap
     WHERE market_slug = $1
     ORDER BY intent
"""

#: THE SIBLING CONTRACTS ON THE SAME FIXTURE, which is the candidate set.
#:
#: `market_slug <> $2` is the NETTING EXCLUSION and it is not a tidy-up: run
#: 264 measured exactly 2.00 rows per market_slug and exactly 2 distinct
#: intents on every one of the venue's seventeen slug prefixes, so the held
#: contract's other side IS the held instrument. Admitting it would present a
#: netting trade as an independent liquidity opportunity.
#:
#: DISTINCT ON (market_slug, intent) keeps BOTH SIDES of every sibling market.
#:
#: THE SECOND DEFECT, MEASURED. This key was
#: `coalesce(team_abbr, side_norm)`, and run 269 measured what that drops:
#:
#:   future slugs ...................................... 16,540
#:   slugs where the OLD key collapsed the two sides ...  5,930  (35.9%)
#:   slugs where `intent` keeps both ................... 16,540  (all)
#:
#: The collapsing case is `team_abbr` present and EQUAL on both rows -- e.g.
#: `aachc-nhl-fewestpts-2027-04-10-ana`, whose yes row and no row are both
#: `ana`. The old key saw one value and returned one row, so on more than a
#: third of tradable instruments the second side was never offered at all --
#: upstream of, and separate from, the identity collapse downstream.
SIBLINGS_SQL = """
    SELECT DISTINCT ON (market_slug, intent)
           market_slug, event_slug, event_title, question, sports_type,
           side_norm, team_abbr, team_name, line, signed, intent, kind,
           game_start, updated_at
      FROM us_premap
     WHERE event_slug = $1
       AND market_slug <> $2
     ORDER BY market_slug, intent, updated_at DESC
     LIMIT $3
"""

#: How many (slug, side) pairs the fixture actually has, so truncation at
#: MAX_CANDIDATE_ROWS is REPORTED rather than looking like the whole set.
SIBLING_COUNT_SQL = """
    SELECT count(DISTINCT (market_slug, intent)) AS pairs,
           count(DISTINCT market_slug)           AS slugs
      FROM us_premap
     WHERE event_slug = $1
       AND market_slug <> $2
"""

#: Bounded because each candidate costs a paced venue read. NOTE THE UNITS: the
#: limit counts (slug, side) PAIRS, and now that both sides are returned it
#: covers half as many instruments as it used to. That is a real reduction in
#: breadth per pass and it is why `candidate_legs_for` reports
#: `truncated_at_limit` with the number of pairs the fixture has -- a silently
#: truncated discovery reads as "these are the candidates" when it is not.
MAX_CANDIDATE_ROWS = 40

#: How many catalogue rows are READ to order the search. A database read, not a
#: venue read: only MAX_CANDIDATE_ROWS of them are ever quoted.
SEARCH_ORDER_ROWS_READ = 400

SEARCH_ORDER_RULE = (
    "rank 0: the other participant's POSITIVE handicap (or the opposite side "
    "of a total) in the SAME period as the held leg, whose line leaves an "
    "overlapping winning region -- the shape of a middle; rank 1: any other "
    "graded contract in the same period; rank 2: another period, which the "
    "grading key never admits as protection for this one; rank 3: a row no "
    "leg can be built from. Ties keep the catalogue's slug order. It orders "
    "what is QUOTED within the unchanged budget and decides nothing")

#: A payout event derived from a built leg names the leg's grading facts.
PAYOUT_EVENT_BASIS = "BUILT_LEG_GRADING_FACTS"


def payout_event_of_leg(leg) -> str | None:
    """WHAT A BUILT LEG PAYS ON, stated from its own grading facts.

    Not a bookmaker's outcome name -- no external source prices most hedge
    contracts -- but the exact event the venue grades this side against:
    fixture, period, variable, which participant it backs and at what line,
    the direction of a total and the overtime treatment. Carried onto the
    hedge intent so the servicing pass can value the leg; a probability row
    whose payout event does not match it is still refused by `ev_hold`, which
    is the honest outcome when nothing prices this exact event."""
    if leg is None or getattr(leg, "condition_id", None) is None:
        return None
    parts = []
    for name in ("condition_id", "fixture_id", "period", "kind", "backs",
                 "line", "over_under", "overtime"):
        v = getattr(leg, name, None)
        if v is not None:
            parts.append("%s=%s" % (name, v))
    return "PAYS_ON(%s)" % ";".join(parts)


def search_priority(row, held_row) -> dict:
    """The search rank of one catalogue row against the held leg's row. Pure.

    Read from the catalogue alone (no quote, no prose), so it can order what
    is quoted. It never admits anything: `discover` and the whole-position
    valuation decide on the built legs."""
    cand = derive_kind(row)
    held = derive_kind(held_row)
    if cand.get("refusal"):
        return {"rank": 3, "why": cand.get("refusal")}
    if held.get("refusal") or held.get("period") != cand.get("period"):
        return {"rank": 2,
                "why": ("a different graded period (%s against the held %s): "
                        "never admitted as protection for this position"
                        % (cand.get("period"), held.get("period")))}
    h_team = _clean((held_row or {}).get("team_abbr"))
    c_team = _clean((row or {}).get("team_abbr"))
    if cand.get("kind") == IS.KIND_SPREAD and c_team and h_team \
            and c_team != h_team \
            and held.get("kind") in (IS.KIND_MONEYLINE, IS.KIND_SPREAD):
        c_line = cand.get("signed_line")
        h_line = (held.get("signed_line") if held.get("kind")
                  == IS.KIND_SPREAD else 0)
        if c_line is not None and h_line is not None \
                and (c_line + h_line) > 0:
            return {"rank": 0,
                    "why": ("the other participant at %+g against a held "
                            "%s%s: both can win when the held side wins by "
                            "less than the handicap"
                            % (float(c_line), held.get("kind"),
                               "" if not h_line
                               else " %+g" % float(h_line)))}
    if cand.get("kind") == IS.KIND_TOTAL and held.get("kind") == IS.KIND_TOTAL:
        h_ou, c_ou = held.get("over_under"), cand.get("over_under")
        h_l, c_l = held.get("line"), cand.get("line")
        if None not in (h_l, c_l) and h_ou != c_ou and (
                (h_ou == "OVER" and c_l > h_l)
                or (h_ou == "UNDER" and c_l < h_l)):
            return {"rank": 0,
                    "why": ("the opposite side of the total with a line that "
                            "leaves a band where both win")}
    return {"rank": 1, "why": "a graded contract in the held leg's period"}

R_NO_EVENT_FOR_HELD = "THE_HELD_CONTRACTS_ROW_NAMES_NO_EVENT_TO_FIND_SIBLINGS_ON"
R_CATALOGUE_READ_FAILED = "THE_CATALOGUE_READ_ITSELF_FAILED"


async def read_row(conn, market_slug, side):
    """The catalogue row for ONE SIDE of one instrument, or None.

    `side` is required and is not defaulted. A caller that does not know which
    side it holds must refuse, because picking one is picking a payout function
    at random -- which is exactly what `LIMIT 1` was doing.
    """
    if side not in SIDES:
        return {"error": "SIDE_NOT_ONE_OF_%s" % (SIDES,)}
    try:
        got = await conn.fetchrow(ROW_SQL, str(market_slug or ""), str(side))
    except Exception as exc:                                    # noqa: BLE001
        return {"error": type(exc).__name__}
    return dict(got) if got is not None else None


async def read_sides(conn, market_slug):
    """Which sides the catalogue actually has for this slug. For reporting."""
    try:
        rows = await conn.fetch(ROW_SIDES_SQL, str(market_slug or ""))
    except Exception:                                           # noqa: BLE001
        return []
    return [dict(r) for r in rows]


def held_side_of(position) -> dict:
    """Which side of its instrument this funded position holds.

    `bettor_funded_intents.order_intent` is the field, and run 268 measured its
    vocabulary to be the SAME two values `us_premap.intent` takes, so the join
    is direct rather than a mapping to be invented.

    ABSENT IS A REFUSAL. Production currently holds ZERO funded intents (run
    268, statements 4 and 5, both empty), so there is no live position to read
    this off and no measurement can tell me what a real one will carry. Guessing
    would put a coin flip between the venue and the payout function.
    """
    pos = dict(position or {})
    stated = _side_token(pos.get("order_intent"))
    out = {"side": None, "refusal": None, "stated": stated,
           "source": "bettor_funded_intents.order_intent",
           "vocabulary": SIDES}
    if stated in SIDES:
        out["side"] = stated
        out["why"] = ("the position states %s, which is one of the two values "
                      "us_premap.intent takes" % stated)
        return out
    out["refusal"] = R_HELD_SIDE_NOT_STATED
    out["why"] = (
        "the position states order_intent=%r, which is not one of %s. The "
        "instrument has exactly two sides and they have opposite payout "
        "functions -- on a spread the same row pair carries signed -1.5 and "
        "+1.5 -- so a leg built without knowing the side is a payout function "
        "chosen at random. This used to be `LIMIT 1`" % (stated, SIDES))
    return out


async def held_leg_for(conn, *, position, prose_reader=None, now=None) -> dict:
    """THE HELD POSITION'S LEG, from the real catalogue and the real prose.

    `prose_reader` is an awaitable taking the slug and returning the result of
    `bettor_live_read.read_rules_text` -- injected so the venue transport can
    be substituted in a test WITHOUT substituting this function, the builder,
    or the readers it calls. That distinction is the whole point: the thing
    under test is the supplier, and only the network is stood in for.
    """
    pos = dict(position or {})
    slug = _clean(pos.get("us_market_slug"))
    out = {"ok": False, "leg": None, "refusal": None, "us_market_slug": slug,
           "source": "us_premap + venue settlement prose"}
    if not slug:
        out.update(refusal=R_NO_CATALOGUE_ROW,
                   why="the position names no venue market slug")
        return out
    # ── WHICH SIDE, BEFORE ANY ROW IS READ ───────────────────────────
    side_read = held_side_of(pos)
    out["held_side"] = side_read
    if side_read["refusal"]:
        out.update(refusal=side_read["refusal"], why=side_read["why"])
        return out
    side = side_read["side"]
    out["side"] = side
    out["candidate_id"] = candidate_identity(slug, side)
    row = await read_row(conn, slug, side)
    if row is None:
        # THE SIDE ASKED FOR IS NOT IN THE CATALOGUE. Distinguish that from
        # "no row for this slug at all": the first is a data gap on one side
        # and the second is an unknown instrument, and they are fixed
        # differently.
        available = await read_sides(conn, slug)
        if available:
            out.update(
                refusal=R_HELD_SIDE_NOT_IN_CATALOGUE,
                sides_available=[r.get("intent") for r in available],
                why=("the catalogue has %d row(s) for %r but none with "
                     "intent=%s. The position states a side the catalogue does "
                     "not carry, and the other side is a different contract"
                     % (len(available), slug, side)))
            return out
    if isinstance(row, dict) and row.get("error"):
        out.update(refusal=R_CATALOGUE_READ_FAILED,
                   why=("the catalogue read failed (%s). Unread is not empty: "
                        "no leg is built and no hedge is discovered, and the "
                        "exit path is untouched" % row["error"]))
        return out
    if row is None:
        out.update(refusal=R_NO_CATALOGUE_ROW,
                   why=("the venue catalogue has no row for %r, so the held "
                        "contract's kind, period and orientation are all "
                        "unestablished" % slug))
        return out
    out["row"] = {k: row.get(k) for k in ("sports_type", "event_slug",
                                          "team_abbr", "side_norm", "signed",
                                          "line", "game_start")}
    prose, psource, age = None, None, None
    if prose_reader is not None:
        try:
            pr = await prose_reader(slug)
        except Exception as exc:                                # noqa: BLE001
            pr = {"ok": False, "error": type(exc).__name__}
        pr = dict(pr or {})
        prose = pr.get("rules_text")
        psource = pr.get("source") or pr.get("error")
        out["prose_read"] = {"ok": bool(pr.get("ok")),
                             "field": pr.get("rules_field"),
                             "chars": len(str(prose or "")),
                             "error": pr.get("error"),
                             "from_cache": pr.get("from_cache")}
        if pr.get("read_at") is not None and now is not None:
            age = round(float(now) - float(pr["read_at"]), 3)
    basis = await held_basis_per_unit(conn, pos)
    out["basis"] = basis
    built = build_leg(
        row=row, quantity=(pos.get("residual_qty") or pos.get("filled_qty")),
        cost_per_unit=basis.get("cost_per_unit"),
        prose=prose, prose_source=psource, evidence_age_s=age,
        sport_family=_clean(str(row.get("sports_type") or "").split("_")[0]))
    out.update(built)
    if built.get("ok"):
        out.setdefault("built_from", {})["cost_cents_per_unit"] = (
            basis.get("source"))
    out["ok"] = bool(built.get("ok"))
    return out


#: Where the held leg's per-unit cost came from.
BASIS_FROM_FILLS = "FB.remaining_basis: entry-fill cash / entry-fill quantity"
BASIS_FROM_LIMIT_NO_FILL = ("the entry order's limit, converted to the held "
                            "side's cost: no entry fill carries a cost yet")


async def held_basis_per_unit(conn, position) -> dict:
    """THE HELD LEG'S PER-CONTRACT COST, ON THE SAME BASIS HOLD USES.

    THE DEFECT THIS CLOSES (Xavier map Q3). The held leg was costed at
    `avg_price or limit_price`. The intents table has no `avg_price`, so it was
    the entry order's WIRE limit: on an entry that filled better than its
    limit the hedge was valued against a cost the position never paid, and on
    a SHORT the YES-denominated wire (0.40) stood in for the short's actual
    cost (0.60) -- `_cents_per_unit` converts nothing. HOLD, EXIT and REDUCE
    are all scored on `FB.remaining_basis`, so the same leg was valued on two
    different costs depending on which action was being compared.

    The fills ledger's cash is already in COST space on both sides
    (`live_executor.fill_cash`: price x qty on a long, (1 - price) x qty on a
    short), so the per-contract basis needs no further conversion. Only a
    position with NO entry fill falls back to its limit -- converted to the
    side held -- and says so; such a position holds nothing yet.
    """
    pos = dict(position or {})
    out = {"cost_per_unit": None, "source": None, "basis_per_contract": None}
    iid = pos.get("intent_id")
    if conn is not None and iid:
        try:
            from . import bettor_funded_book as _FB

            rb = await _FB.remaining_basis(conn, str(iid))
        except Exception as exc:                                # noqa: BLE001
            rb = {"error": type(exc).__name__}
        out["remaining_basis"] = rb
        per = (rb or {}).get("basis_per_contract")
        if per is not None:
            out.update(cost_per_unit=float(per), basis_per_contract=float(per),
                       source=BASIS_FROM_FILLS)
            return out
    lim = pos.get("limit_price")
    if lim is None:
        return out
    wire = float(lim)
    held = _side_token(pos.get("order_intent"))
    out.update(cost_per_unit=(round(1.0 - wire, 6) if held == SIDE_SHORT
                              else wire),
               source=BASIS_FROM_LIMIT_NO_FILL, wire_limit=wire,
               held_side=held)
    return out


async def _quote_side(quoter, market_slug, side):
    """Call once on the requested side; a transport TypeError is not a retry.

    Legacy slug-only readers remain identifiable for diagnostics, but cannot
    establish a side-specific execution price.
    """
    import inspect
    side_aware = True
    try:
        inspect.signature(quoter).bind(market_slug, side)
    except TypeError:
        side_aware = False
    except (ValueError, AttributeError):
        pass
    try:
        got = await quoter(market_slug, side) if side_aware else await quoter(market_slug)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}, side_aware
    return dict(got or {}), side_aware


async def candidate_legs_for(conn, *, held_row, quoter=None,
                             prose_reader=None, limit=None, now=None,
                             prefilter=None) -> dict:
    """EVERY ELIGIBLE COMPLEMENTARY CONTRACT ON THE SAME FIXTURE, built.

    Returns {"legs": [...], "refused": [...], "examined": n, ...}. EVERY
    sibling is attempted and every failure is reported with the fact it
    lacked, because "the first admitted contract" is what Codex asked this not
    to be: a ranking over one candidate is not a ranking.

    `quoter` is an awaitable taking the slug and returning that contract's own
    price and depth; `prose_reader` its settlement text. Both are injected so
    the venue transport can be substituted without substituting the supplier.
    A candidate with no price is REFUSED rather than priced from the held
    contract's book -- two instruments do not share a ladder.
    """
    hr = dict(held_row or {})
    event = _clean(hr.get("event_slug"))
    held_slug = _clean(hr.get("market_slug"))
    out = {"legs": [], "refused": [], "examined": 0, "event_slug": event,
           "held_excluded": held_slug, "refusal": None,
           "every_candidate_was_attempted": True,
           "netting_exclusion": (
               "the held market_slug is excluded because the venue carries ONE "
               "instrument per market -- run 264 measured exactly 2.00 rows "
               "and 2 intents per slug on all seventeen prefixes -- so its "
               "other side is the held instrument, not a second holding")}
    if not event:
        out.update(refusal=R_NO_EVENT_FOR_HELD,
                   why="the held contract's row names no event")
        return out
    cap = int(limit or MAX_CANDIDATE_ROWS)
    try:
        # THE CATALOGUE ROWS ARE CHEAP; THE QUOTES ARE NOT. Enough rows are
        # read to ORDER the search, and only `cap` of them are then quoted --
        # so the paced venue-read budget is exactly what it was.
        fetched = await conn.fetch(SIBLINGS_SQL, event, held_slug,
                                   max(cap, SEARCH_ORDER_ROWS_READ))
    except Exception as exc:                                    # noqa: BLE001
        out.update(refusal=R_CATALOGUE_READ_FAILED,
                   why=("the sibling read failed (%s); no candidate is "
                        "discovered and the exit path is untouched"
                        % type(exc).__name__))
        return out
    # ── THE SEARCH ORDER: LIKELY OVERLAPPING STRUCTURES ARE QUOTED FIRST ──
    #
    # A SEARCH ORDER, NOT A PURCHASE RULE. Within the same read budget, the
    # opponent's positive handicap in the SAME game and period (the shape of a
    # middle against a held moneyline or spread) and the opposite side of a
    # total are examined before the rest, so the budget is spent where an
    # overlapping winning region is possible. Nothing is admitted, preferred
    # or bought for being here: admission is `discover`'s, and the decision is
    # still whole-position expected value against HOLD, EXIT and REDUCE. A
    # row from another period sorts last because it can never be protection
    # for this one -- the grading key refuses it.
    keyed = []
    for i, raw in enumerate(fetched):
        pr = search_priority(dict(raw), hr)
        keyed.append((pr["rank"], i, pr, raw))
    keyed.sort(key=lambda k: (k[0], k[1]))
    rows = [k[3] for k in keyed][:cap]
    priorities = {str(k[1]): k[2] for k in keyed}
    out["search_order"] = {
        "rule": SEARCH_ORDER_RULE,
        "catalogue_rows_read": len(fetched), "quoted_at_most": cap,
        "by_rank": {r: sum(1 for k in keyed if k[0] == r)
                    for r in sorted({k[0] for k in keyed})},
        "is_a_purchase_rule": False}
    out["examined"] = len(rows)
    # ── TRUNCATION IS REPORTED, NEVER SILENT ─────────────────────────
    #
    # The limit counts (slug, side) PAIRS, and the DISTINCT ON key now returns
    # both sides, so the same limit covers half as many instruments as before.
    # A discovery that stopped at the limit is not "the candidate set"; it is a
    # prefix of it, and saying so is the difference between "nothing better
    # exists" and "we did not look".
    try:
        tot = await conn.fetchrow(SIBLING_COUNT_SQL, event, held_slug)
    except Exception:                                           # noqa: BLE001
        tot = None
    have_pairs = None if tot is None else int(tot["pairs"])
    out["fixture_candidate_pairs"] = have_pairs
    out["fixture_candidate_slugs"] = (None if tot is None
                                      else int(tot["slugs"]))
    out["limit"] = cap
    out["truncated_at_limit"] = bool(
        len(rows) >= cap and max(have_pairs or 0, len(fetched)) > len(rows))
    if out["truncated_at_limit"]:
        out["truncation_note"] = (
            "this fixture has %s (slug, side) candidate pairs and the read "
            "stopped at %d. The candidates below are a PREFIX -- in the "
            "search order above, then by slug -- not the fixture's best ones: "
            "nothing here supports 'no better candidate exists'"
            % (have_pairs if have_pairs is not None else len(fetched), cap))
    index_of = {id(k[3]): k[1] for k in keyed}
    for raw in rows:
        row = dict(raw)
        priority = priorities.get(str(index_of.get(id(raw))), {})
        slug = _clean(row.get("market_slug"))
        side = side_of(row)
        cid = candidate_identity(slug, side) if side else slug
        # PRICE AND DEPTH ARE THIS CONTRACT'S OWN, AND THIS SIDE'S OWN. A
        # candidate priced off another instrument's ladder is a fabricated
        # cost; a candidate priced off the OTHER SIDE of its own instrument is
        # the same error inside one market -- the two sides consume opposite
        # sides of one book and have different acquisition costs.
        # SCREENED BEFORE IT COSTS A READ, when the caller supplies the
        # screen. `prefilter(row)` returns None or the name of the fact the
        # row lacks; it applies the predicates `build_leg` would apply after
        # the read (graded variable, fixture identity, orientation) plus
        # realism, so it can only refuse earlier, never admit more.
        if prefilter is not None:
            screened = prefilter(row)
            if screened:
                out["refused"].append(
                    {"candidate_id": cid, "market_slug": slug, "side": side,
                     "sports_type": row.get("sports_type"),
                     "refusal": str(screened),
                     "why": ("refused by the caller's screen before any venue "
                             "read: the row cannot become a leg (%s)"
                             % screened)})
                continue
        price, depth, quote = None, None, {}
        quote_is_side_aware = None
        if quoter is not None:
            quote, quote_is_side_aware = await _quote_side(quoter, slug, side)
            price = quote.get("cost_per_share") or quote.get("price")
            depth = quote.get("depth_qty")
        from . import bettor_book_snapshot as _bs
        if price is None and quote.get("refusal") in _bs.GRID_REFUSALS:
            # ── NO DEPTH AN ORDER WE CAN SEND IS ABLE TO REACH ───────────
            #
            # Refused by the GRID'S OWN NAME, with the levels it excluded, so
            # the hedge search's record says "unrepresentable" -- not the
            # generic "not priced", which would read as a missing quote.
            out["refused"].append(
                {"candidate_id": cid, "market_slug": slug, "side": side,
                 "sports_type": row.get("sports_type"),
                 "refusal": quote["refusal"],
                 "executable_grid": quote.get("executable_grid"),
                 "levels_excluded_unrepresentable": quote.get(
                     "levels_excluded_unrepresentable"),
                 "excluded_unrepresentable_qty": quote.get(
                     "excluded_unrepresentable_qty"),
                 "why": ("%s. The displayed depth is not reachable by any "
                         "order we can send, so it is excluded before "
                         "valuation and sizing and the candidate is not "
                         "rankable" % (quote.get("why") or quote["refusal"]))})
            continue
        if price is None:
            out["refused"].append(
                {"candidate_id": cid, "market_slug": slug, "side": side,
                 "sports_type": row.get("sports_type"),
                 "refusal": R_CANDIDATE_NOT_PRICED,
                 "why": ("this contract's own price was not established (%s). "
                         "Pricing it off the held contract's ladder would "
                         "invent the cost of the hedge"
                         % (quote.get("refusal") or quote.get("error")
                            or "no quoter supplied"))})
            continue
        if quote.get("executable_grid") is not None and not \
                _bs.on_executable_grid(quote.get("api_price"),
                                       quote.get("executable_grid")):
            # A FUNDED QUOTE (one that carries its grid) whose price is off the
            # grid would be rounded to a whole cent in `build_leg` -- 0.985
            # becomes 98 cents -- and valued at a price nobody offered.
            out["refused"].append(
                {"candidate_id": cid, "market_slug": slug, "side": side,
                 "sports_type": row.get("sports_type"),
                 "refusal": _bs.R_LIMIT_OFF_THE_EXECUTABLE_GRID,
                 "why": ("the quoted wire price %r is not on the executable "
                         "grid %r; its cost would be rounded into the leg"
                         % (quote.get("api_price"),
                            (quote.get("executable_grid") or {}).get(
                                "step")))})
            continue
        prose, psource, age = None, None, None
        if prose_reader is not None:
            try:
                pr = dict(await prose_reader(slug) or {})
            except Exception as exc:                            # noqa: BLE001
                pr = {"ok": False, "error": type(exc).__name__}
            prose = pr.get("rules_text")
            psource = pr.get("source") or pr.get("error")
            if pr.get("read_at") is not None and now is not None:
                age = round(float(now) - float(pr["read_at"]), 3)
        built = build_leg(
            row=row, quantity=(quote.get("available_qty")
                               or depth or hr.get("residual_qty") or 1),
            cost_per_unit=price, prose=prose, prose_source=psource,
            evidence_age_s=age,
            sport_family=_clean(str(row.get("sports_type")
                                    or "").split("_")[0]))
        if not built.get("ok"):
            out["refused"].append(
                {"candidate_id": cid, "market_slug": slug, "side": side,
                 "sports_type": row.get("sports_type"),
                 "refusal": built.get("refusal"), "field": built.get("field"),
                 "why": built.get("why")})
            continue
        out["legs"].append(
            {"leg": built["leg"],
             # THE IDENTITY, AND THE VENUE ADDRESS, SEPARATELY.
             "candidate_id": built["candidate_id"],
             "market_slug": built["venue_slug"],
             "side": built["side"],
             "sports_type": row.get("sports_type"),
             "built_from": built["built_from"],
             "grading_key": built["grading_key"],
             "price": price, "depth_qty": depth,
             "price_is_this_sides_own": quote_is_side_aware,
             "evidence_age_s": age, "quote": quote,
             # WHAT THIS CONTRACT PAYS ON, from the leg's own grading facts,
             # so a hedge acquired from it is a position the next servicing
             # pass can value (it refused R_NO_PAYOUT_EVENT forever before).
             "payout_event": payout_event_of_leg(built["leg"]),
             "payout_event_basis": PAYOUT_EVENT_BASIS,
             "search_priority": priority,
             "settlement_text_captured": built["settlement_text_captured"]})
    # ── THE ONE CHECK THAT NEEDS BOTH ROWS OF AN INSTRUMENT ──────────
    #
    # WHERE THIS CAME FROM. `orientation_of` reads `team_abbr` alone. Run 269
    # measured 5,930 future slugs whose two rows carry the SAME team_abbr, and
    # on such a pair both sides would come back with the SAME `backs` -- two
    # legs claiming to back one participant while holding opposite outcome
    # tokens, one of them with its payout function inverted. Exercised against a
    # fixture of that shape, that is exactly what happened.
    #
    # Run 270 then measured whether it reaches a market we grade. On the graded
    # prefixes (aec-/asc-/tsc-), of 5,974 future slugs:
    #
    #     team_abbr absent on both ....  2,159  (orientation refuses, correctly)
    #     team_abbr differs ...........  3,347  (orientation established)
    #     team_abbr EQUAL .............    468  -- and every one is a TEAM TOTAL
    #     side_norm differs ...........  5,974  (all of them)
    #
    # A total has no orientation: `Leg` carries `over_under` and `build_leg`
    # does not call `orientation_of` for KIND_TOTAL. So on every leg that
    # actually uses orientation, `team_abbr` either distinguishes the sides or
    # is absent and refuses -- the collapse does not reach a payout function
    # TODAY.
    #
    # It is guarded anyway, because "today" is a fact about the catalogue and
    # not about this code: a provider that starts emitting equal team codes on a
    # spread would otherwise invert a payout silently. This is the only place
    # both rows of one instrument are in scope, so it is the only place the
    # contradiction is visible.
    by_instrument: dict[str, list[dict]] = {}
    for entry in out["legs"]:
        by_instrument.setdefault(entry["market_slug"], []).append(entry)
    contradictions = []
    for slug_, entries in by_instrument.items():
        oriented = [e for e in entries
                    if getattr(e["leg"], "backs", None) is not None]
        if len(oriented) < 2:
            continue
        if len({e["leg"].backs for e in oriented}) == 1:
            contradictions.append(slug_)
    if contradictions:
        kept = []
        for entry in out["legs"]:
            if entry["market_slug"] not in contradictions:
                kept.append(entry)
                continue
            out["refused"].append({
                "candidate_id": entry["candidate_id"],
                "market_slug": entry["market_slug"], "side": entry["side"],
                "sports_type": entry.get("sports_type"),
                "refusal": R_BOTH_SIDES_CLAIM_ONE_ORIENTATION,
                "why": ("both sides of %r built a leg backing %r. The two sides "
                        "of one instrument hold opposite outcome tokens, so one "
                        "of these payout functions is inverted and the row does "
                        "not say which. Orientation comes from team_abbr, and "
                        "this instrument states the same code on both rows"
                        % (entry["market_slug"], entry["leg"].backs))})
        out["legs"] = kept
        out["orientation_contradictions"] = sorted(contradictions)
    out["why"] = ("%d of %d sibling contracts on this fixture were built into "
                  "legs; %d refused, each naming the fact it lacked"
                  % (len(out["legs"]), out["examined"], len(out["refused"])))
    return out


def describe() -> dict:
    return {"version": VERSION,
            "supplies": ("held_leg and candidate_legs for "
                         "bettor_funded_pair_cycle.discover"),
            "field_sources": {k: v[0] for k, v in LEG_FIELD_SOURCES.items()},
            "refusal_per_field": {k: v[1] for k, v in
                                  LEG_FIELD_SOURCES.items()},
            "what_is_not_supplied_here": (
                "region_probabilities. It needs an APPROVED model and the "
                "production registry is empty, so the supplier returns "
                "nothing until one is promoted through the prospective "
                "non-funded evidence path. Venue-implied prices must not be "
                "substituted: that is the independently validated edge the "
                "directive forbids manufacturing"),
            "the_overtime_label_trap": (
                "OVERTIME_PROSE's includes/excludes are relative to the BOOK "
                "rule, not to overtime. Read literally they give every "
                "soccer leg the opposite treatment")}
