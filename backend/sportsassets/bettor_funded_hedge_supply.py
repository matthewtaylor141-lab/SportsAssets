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
    "tie_rule": ("the same captured venue prose. Absent prose leaves the "
                 "TIE region's payout UNDETERMINED, which propagates as "
                 "UNESTABLISHABLE -- it is never filled in from memory",
                 None),
    "void_rule": ("the same captured venue prose, same treatment", None),
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
