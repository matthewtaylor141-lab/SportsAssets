"""THE PRICED SETTLEMENT-DIFFERENCE POLICY: A BOOK PROBABILITY AS A VENUE
CONTRACT'S WORST-CASE VALUE WHEN THE TWO SIDES SETTLE EXCEPTIONAL GAMES
DIFFERENTLY.

THE REFUSAL THIS PRICES (P1 first-loss census, production 2026-10-06).
SETTLEMENT_NOT_SUPPORTED was the first loss of 29 events in one hour, every
one written by DEREK_ENTRY_POLICY_V2 (research-sql run 37479304426 section 4:
unl 882, mlb 255, nfl 228, kbl 117, nba 107, cfb 71, eurocup 54, bcl 46,
nhl 21, khl 18, eurolg 17, lnbp 9 decisions in 24 h). The cause is one
documented difference, the same on every venue listing of these leagues:

  venue  "If the game is delayed, postponed, or suspended and not
         rescheduled to a date within two weeks of the originally scheduled
         date, the market will settle to the last fair market price."
         (every basketball / hockey listing: tests/fixtures/
         pmus_basketball_hockey_winner_listings_2026_10_06.json; NFL:
         bettor_nfl_settlement.Q_VENUE_POSTPONED; NCAAF:
         bettor_ncaaf_settlement.Q_VENUE_POSTPONED)
  book   the stake is returned on a game not played / not completed
         (bettor_settlement_terms.BOOK_TERMS[...][C_NOT_PLAYED], each cited
         to the captured Pinnacle rules; e.g. basketball "43 minutes" /
         "35 minutes", American football "If a game is not started within
         12 hours of its originally scheduled time all bets will be voided.")

`bettor_venue_settlement.attest` reads that as a payout MISMATCH at
POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED (book: stake back; venue: last
fair market price) and the strict policy refuses. That refusal stays true:
the payouts DO differ. What was missing is a price for the difference.

THE PRICE. Partition every fixture into O (ordinarily completed -- both
sides grade it identically, which the completed-game match PROVES from the
cited texts: the same grading period, overtime / extra innings included
where both say so, the NFL tie priced by its own cited interval, the NCAAF
no-tie rule) and X (everything else: postponed, abandoned, suspended,
called, forfeited, relocated, or any other state where either side may pay
differently). The venue contract pays its holder some S in [0, 1] in X --
whatever the venue's text says or does not say, a binary contract pays
neither less than 0 nor more than 1. The book's de-vigged probability
p_book is P(the paid event | the bet has action), and the book has action on
every O fixture. So, with q = P(X):

    E[venue payout] >= P(paid event AND O)
                     = p_book * P(action) - P(paid event AND X with action)
                    >= p_book * (1 - q_void) - q_action
                    >= p_book - q

(q_void + q_action = q; p_book <= 1). The bound holds WHATEVER either side
pays in X -- it never needs the venue's or the book's exceptional payouts,
only an upper bound on how often X happens. The policy values the contract
at

    p_venue = max(0, p_completed - q_hi)

where p_completed is the book's number after the ordinary-completion
conversion (unchanged except for the NFL tie, bettor_nfl_settlement's worst
end), and q_hi is the conservative upper bound below. Never the favourable
end, never a point estimate.

q_hi, MEASURED AND THEN FLOORED. The venue's own settlements of the
fixtures this lane valued (research-sql run 37479304426 section 1,
fixture level, settlement_exception_risk.classify_market's classes): a
fixture the venue settled at a price strictly inside (0, 1) or declared
void is an X fixture the venue's record can SEE. Per family:

    baseball    1 of 65   (aec-mlb-bal-nyy-2026-09-27 settled at 0.485)
    soccer      0 of 65
    football    0 of 15
    basketball  0 of 4
    hockey      0 of 2

The record cannot see an X fixture the venue settled in full (a game
rescheduled inside its two-week window, a forfeit graded by the governing
body), so the count is a LOWER count of X. q_hi is therefore the larger of
the measured upper 95% bound (the larger of Wilson and Clopper-Pearson,
`settlement_exception_risk.interval`) and the conservative prior floor
`settlement_exception_risk.PRIOR_FLOOR_UPPER` -- the exact 95% upper bound
of zero events in 40 fixtures, 8.81% -- so no family is priced as safer
than the least informative admissible measurement. Every family's q_hi is
the floor today; the table carries the counts so a larger measurement can
only raise it.

ADMITTED ONLY THROUGH THIS POLICY, NEVER AS COMPATIBILITY. `price` returns
the policy id, version, the formula, the derivation, the rate's counts and
basis, and the citations; a caller records them on the decision and passes
the settlement to the capital gate as PRICED_SETTLEMENT_DIFFERENCE (never
COMPATIBLE). A difference this bound cannot price refuses by its exact
code:

  SETTLEMENT_DIFFERENCE_ORDINARY_COMPLETION_DIFFERS  a payout MISMATCH in an
      ordinarily completed game (regulation / after regulation): the bound
      is about X, never O
  SETTLEMENT_DIFFERENCE_ORDINARY_COMPLETION_NOT_ESTABLISHED  the completed-
      game match did not prove the two grade O identically (its own
      refusals ride behind)
  SETTLEMENT_DIFFERENCE_LANE_CODE_NOT_PRICEABLE  a settlement-stage lane
      refusal that is not an exceptional-state difference
  SETTLEMENT_DIFFERENCE_CLAUSE_NOT_THE_CITED_ONE  (NCAAF) the venue's text
      is not exactly the cited clauses
  SETTLEMENT_DIFFERENCE_MARKET_NOT_A_MONEY_LINE  a line market (its own
      exceptional terms, bettor_market_family)
  SETTLEMENT_DIFFERENCE_FAMILY_RATE_NOT_HELD  no rate row for the family
  SETTLEMENT_DIFFERENCE_NO_BOOK_PROBABILITY  nothing to price

PURE. Standard library plus the pure rate helpers of
settlement_exception_risk and the condition names of bettor_settlement_terms.
"""
from __future__ import annotations

import math

from . import bettor_settlement_terms as ST
from . import settlement_exception_risk as SER

POLICY_ID = "PRICED_SETTLEMENT_DIFFERENCE"
VERSION = "PRICED_SETTLEMENT_DIFFERENCE_V1"
#: what the capital gate is told the settlement is (never COMPATIBLE)
SETTLEMENT_PRICED = "PRICED_SETTLEMENT_DIFFERENCE"
P_IS = ("VENUE_CONTRACT_WORST_CASE_VALUE_P_COMPLETED_MINUS_THE_UPPER_BOUND_"
        "OF_THE_EXCEPTIONAL_STATE_PROBABILITY")
FORMULA = "p_venue = max(0, p_completed - q_hi)"

R_ORDINARY_DIFFERS = "SETTLEMENT_DIFFERENCE_ORDINARY_COMPLETION_DIFFERS"
R_ORDINARY_UNPROVEN = \
    "SETTLEMENT_DIFFERENCE_ORDINARY_COMPLETION_NOT_ESTABLISHED"
R_LANE_CODE = "SETTLEMENT_DIFFERENCE_LANE_CODE_NOT_PRICEABLE"
R_CLAUSE = "SETTLEMENT_DIFFERENCE_CLAUSE_NOT_THE_CITED_ONE"
R_LINE = "SETTLEMENT_DIFFERENCE_MARKET_NOT_A_MONEY_LINE"
R_NO_RATE = "SETTLEMENT_DIFFERENCE_FAMILY_RATE_NOT_HELD"
R_NO_P = "SETTLEMENT_DIFFERENCE_NO_BOOK_PROBABILITY"
REFUSALS = (R_ORDINARY_DIFFERS, R_ORDINARY_UNPROVEN, R_LANE_CODE, R_CLAUSE,
            R_LINE, R_NO_RATE, R_NO_P)
#: NOT a settlement refusal: the contract WAS priced, and its worst-case
#: value is zero (p_completed <= q_hi) -- no price above zero can carry an
#: edge. An economic verdict, classified so.
R_PRICED_AT_ZERO = "SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO"

#: THE MEASURED X FIXTURES PER FAMILY (see the module docstring), with the
#: read that produced them.
RATE_BASIS = {
    "source": ("the venue's own settlements of the fixtures this lane "
               "valued: external_valuations (experiment "
               "EXT_PINNACLE_DEVIG_V1_SHADOW) outcome_basis / settlement_read, "
               "one class per fixture by settlement_exception_risk."
               "classify_market (a price strictly inside (0, 1) or a declared "
               "void is an exceptional fixture)"),
    "read": ("research-sql run 37479304426, research/"
             "p1_first_loss_evidence_3.sql sections 1 and 1b, "
             "2026-10-06T14:29:02Z"),
    "observes": ("only the exceptional fixtures the venue settled at a price "
                 "or voided -- a LOWER count of every exceptional state, "
                 "which is why the prior floor applies"),
}
MEASURED = {
    "baseball": {"k": 1, "n": 65,
                 "k_fixtures": ["aec-mlb-bal-nyy-2026-09-27 (0.485)"]},
    "soccer": {"k": 0, "n": 65, "k_fixtures": []},
    "football": {"k": 0, "n": 15, "k_fixtures": []},
    "basketball": {"k": 0, "n": 4, "k_fixtures": []},
    "hockey": {"k": 0, "n": 2, "k_fixtures": []},
}

#: The ordinarily completed conditions: a MISMATCH here is not priceable.
ORDINARY_CONDITIONS = (ST.C_FULL, ST.C_OVERTIME)
#: The lane's settlement-stage refusals that ARE the exceptional-state
#: difference (or the book's void rule it differs from, not held), plus the
#: NFL tie the completed-game match prices by its cited interval.
PRICEABLE_LANE_CODES = frozenset((
    "VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE",
    "VOID_ABANDONMENT_RULE_NOT_ESTABLISHED",
    "VOID_ABANDONMENT_BOOK_RULE_NOT_HELD",
))
NFL_TIE_LANE_CODE = "DRAW_HANDLING_NOT_RECONCILED"
#: the NCAAF strict codes (bettor_ncaaf_settlement.STRICT_CODES, pinned by a
#: test): postponement, suspension, a tied result declared without a winner
#: (unreachable in a completed game by the cited rule), forfeit, venue
#: change -- every one an X state
NCAAF_X_CODES = frozenset((
    "NCAAF_STRICT_POSTPONEMENT_PAYOUTS_DIFFER",
    "NCAAF_STRICT_SUSPENSION_PAYOUTS_DIFFER",
    "NCAAF_STRICT_TIED_RESULT_VENUE_PAYOUT_NOT_STATED",
    "NCAAF_STRICT_FORFEIT_PAYOUTS_DIFFER",
    "NCAAF_STRICT_VENUE_CHANGE_VENUE_PAYOUT_NOT_STATED",
))

CITATIONS = {
    "venue_postponement_clause": (
        "If the game is delayed, postponed, or suspended and not rescheduled "
        "to a date within two weeks of the originally scheduled date, the "
        "market will settle to the last fair market price."),
    "venue_source": ("tests/fixtures/pmus_basketball_hockey_winner_listings_"
                     "2026_10_06.json (every basketball / hockey league's "
                     "wording); bettor_nfl_settlement.VENUE_CAPTURES; "
                     "bettor_ncaaf_settlement venue captures"),
    "book_source": ("bettor_settlement_terms.BOOK_TERMS[(family, 'h2h', "
                    "context)][POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED], "
                    "cited to the captured Pinnacle rules"),
}


def rate(family) -> dict:
    """{held, q_hi, measured {k, n, upper_95 ...}, floor, basis} for one
    sport family. Pure."""
    fam = str(family or "").strip().lower()
    m = MEASURED.get(fam)
    if m is None:
        return {"held": False, "refusal": R_NO_RATE, "family": fam,
                "why": "no measured row for family %r (held: %s)"
                       % (fam, sorted(MEASURED))}
    iv = SER.interval(m["k"], m["n"])
    floor = float(SER.PRIOR_FLOOR_UPPER)
    if m["n"] >= SER.MIN_FIXTURES:
        q_hi = max(floor, float(iv["upper_95"]))
        status = ("PRIOR_FLOOR" if q_hi == floor
                  else "MEASURED_UPPER_95")
    else:
        # A CELL BELOW THE FLOOR IS AN ANECDOTE (settlement_exception_risk:
        # "a rate from a handful of fixtures is an anecdote with a decimal
        # point"): it takes the conservative prior -- the floor, or the
        # largest upper bound any evidenced family holds, whichever is
        # larger, so an unmeasured family is never cheaper than a measured
        # one.
        q_hi = max([floor] + [
            float(SER.interval(x["k"], x["n"])["upper_95"])
            for x in MEASURED.values() if x["n"] >= SER.MIN_FIXTURES])
        status = "UNMEASURED_CONSERVATIVE_PRIOR"
    return {"held": True, "family": fam, "q_hi": q_hi,
            "q_hi_is": status, "min_fixtures": SER.MIN_FIXTURES,
            "measured": dict(iv, k_fixtures=list(m["k_fixtures"])),
            "prior_floor_upper": floor,
            "prior_floor_basis": ("settlement_exception_risk."
                                  "PRIOR_FLOOR_UPPER: the exact 95%% upper "
                                  "bound of 0 events in %d fixtures"
                                  % SER.MIN_FIXTURES),
            "basis": dict(RATE_BASIS)}


def _codes(xs) -> list:
    return [str(x) for x in (xs or []) if x]


#: The completed-game match's checks that are NOT about how the two sides
#: grade the ordinarily completed game -- identity, the payout outcome, the
#: lane's probability refusals and the venue -- which the calling policy
#: judges by its own checks; only the rest establish ordinary completion.
NON_SETTLEMENT_MATCH_CHECKS = frozenset((
    "fixture_participants_date_side_period", "payout_outcome_match",
    "probability_qualified_by_the_lane", "polymarket_us_contract"))


def ordinary_completion(match: dict | None) -> dict:
    """The completed-game match read for ordinary completion only:
    {established, refusals, checks, policy}. Pure."""
    m = dict(match or {})
    checks = [c for c in (m.get("checks") or [])
              if c.get("check") not in NON_SETTLEMENT_MATCH_CHECKS]
    refusals = [c.get("refusal") for c in checks
                if not c.get("passed") and c.get("refusal")]
    return {"established": bool(checks) and not refusals,
            "refusals": refusals, "policy": m.get("policy"),
            "checks": [{"check": c.get("check"), "passed": c.get("passed"),
                        "refusal": c.get("refusal")} for c in checks]}


def eligibility(*, sport_family, market, league, settlement: dict,
                lane_codes=(), precise_codes=(), match: dict | None = None
                ) -> dict:
    """WHETHER THE ONLY SETTLEMENT DIFFERENCES ARE EXCEPTIONAL-STATE ONES.

    `settlement` is the candidate's recorded comparison (compatibility,
    per_condition), `lane_codes` the row's settlement-stage lane refusals,
    `precise_codes` derek_policy.strict_settlement_reasons (NCAAF), `match`
    paper_benchmark.completed_game_match on the same candidate. Returns
    {eligible, refusal, refusals, why}. Pure."""
    out = {"eligible": False, "refusal": None, "refusals": [], "why": None}

    def refuse(code, why, extra=()):
        out.update(refusal=code, why=why,
                   refusals=[code] + [c for c in extra if c != code])
        return out

    if str(market or "") != "h2h":
        return refuse(R_LINE, "market %r: a line market's exceptional terms "
                              "are its own (bettor_market_family)" % market)
    st = dict(settlement or {})
    per = dict(st.get("per_condition") or {})
    bad = sorted(c for c, r in per.items() if c in ORDINARY_CONDITIONS
                 and str((r or {}).get("verdict") or "") == ST.V_MISMATCH)
    if bad:
        return refuse(R_ORDINARY_DIFFERS, (
            "the two sides pay an ordinarily completed game differently "
            "(%s): the exceptional-state bound does not cover it" % bad))
    lanes = _codes(lane_codes)
    tie_ok = (str(sport_family or "") == "football"
              and str(league or "") == "nfl")
    unpriced = [c for c in lanes if c not in PRICEABLE_LANE_CODES
                and not (tie_ok and c == NFL_TIE_LANE_CODE)]
    if unpriced:
        return refuse(R_LANE_CODE, (
            "settlement-stage lane refusal(s) %s are not an exceptional-state "
            "difference this policy prices" % unpriced), unpriced)
    precise = _codes(precise_codes)
    clauses = [c for c in precise if c not in NCAAF_X_CODES]
    if clauses:
        return refuse(R_CLAUSE, (
            "the NCAAF venue text is not exactly the cited clauses (%s): the "
            "states it settles are not the ones priced" % clauses), clauses)
    m = ordinary_completion(match)
    if m.get("established") is not True:
        return refuse(R_ORDINARY_UNPROVEN, (
            "the completed-game match did not prove the two sides grade the "
            "ordinarily completed game identically: %s"
            % (m.get("refusals") or "no match recorded")),
            _codes(m.get("refusals")))
    out["ordinary_completion"] = m
    out.update(eligible=True, why=(
        "every recorded difference is an exceptional-state one (%s); the "
        "ordinarily completed game is graded identically (%s)"
        % (sorted(set(lanes) | set(precise)
                  | {c for c, r in per.items()
                     if str((r or {}).get("verdict") or "")
                     == ST.V_MISMATCH}) or ["none"],
           m.get("policy"))))
    return out


def price(p_completed, *, sport_family, p_book=None,
          completed_conversion=None) -> dict:
    """THE PRICE. `p_completed` is the book's number for the event the
    contract pays on after the ordinary-completion conversion (the book's
    own number for every family but the NFL, whose tie is converted at its
    worst end first); `p_book` the stored number before that conversion.
    Returns {applies, p, refusal, policy_id, version, formula, derivation,
    rate, ...}. Pure."""
    base = {"applies": True, "policy_id": POLICY_ID, "version": VERSION,
            "p": None, "refusal": None, "formula": FORMULA, "p_is": P_IS,
            "p_book": (None if p_book is None else float(p_book)),
            "completed_conversion": completed_conversion,
            "citations": dict(CITATIONS)}
    try:
        pc = float(p_completed)
    except (TypeError, ValueError):
        pc = None
    if pc is None or not math.isfinite(pc) or not 0.0 < pc < 1.0:
        return dict(base, refusal=R_NO_P,
                    why="no book probability strictly inside (0, 1)")
    rt = rate(sport_family)
    if not rt.get("held"):
        return dict(base, refusal=rt["refusal"], why=rt.get("why"),
                    rate=rt, p_completed=pc)
    q = float(rt["q_hi"])
    return dict(base, p=round(max(0.0, pc - q), 12), p_completed=pc,
                q_hi=q, rate=rt,
                derivation=(
                    "E[venue payout] >= P(paid event AND ordinary "
                    "completion) = p_book * P(action) - P(paid event AND an "
                    "exceptional state with action) >= p_completed - q, "
                    "whatever either side pays in an exceptional state (a "
                    "binary contract pays in [0, 1]); q at its upper bound "
                    "q_hi, so the value is never the favourable end"))


def verify(*, p, p_book, conv: dict) -> dict:
    """Does the declared price `p` follow from the row's `p_book` by THIS
    policy? Re-derived from the declaration's own numbers and the policy's
    own rate (never trusted): p_completed is p_book itself unless an NFL tie
    conversion is declared inside it, then that conversion's own worst end
    ((1 - t) p_book + payout t, re-derived over its interval). {ok, why}.
    Pure."""
    c = dict(conv or {})
    if c.get("version") != VERSION:
        return {"ok": False, "why": "not this policy's declaration"}
    try:
        pb, pv = float(p_book), float(p)
    except (TypeError, ValueError):
        return {"ok": False, "why": "p or p_book is not a number"}
    inner = c.get("completed_conversion")
    if inner:
        iv = inner.get("tie_rate_interval") or [None, None]
        pay = inner.get("tie_payout_per_contract")
        try:
            lo, hi, pay = float(iv[0]), float(iv[1]), float(pay)
        except (TypeError, ValueError, IndexError):
            return {"ok": False, "why": "the declared tie conversion is "
                                        "not a valid interval and payout"}
        pc = min((1.0 - lo) * pb + pay * lo, (1.0 - hi) * pb + pay * hi)
    else:
        pc = pb
    rt = rate(c.get("sport_family"))
    if not rt.get("held"):
        return {"ok": False, "why": rt.get("why")}
    expected = max(0.0, pc - float(rt["q_hi"]))
    ok = abs(expected - pv) <= 1e-9 and \
        abs(float(c.get("q_hi") or -1.0) - float(rt["q_hi"])) <= 1e-12
    return {"ok": ok, "expected": expected, "p_completed": pc,
            "q_hi": rt["q_hi"],
            "why": ("re-derived %s = %.12f" % (FORMULA, expected)) if ok
            else ("declared p %.12f is not %s = %.12f at the policy's own "
                  "q_hi %.6f" % (pv, FORMULA, expected, rt["q_hi"]))}


def describe() -> dict:
    return {"policy_id": POLICY_ID, "version": VERSION, "formula": FORMULA,
            "refusals": list(REFUSALS),
            "rates": {f: {"q_hi": rate(f)["q_hi"], "k": MEASURED[f]["k"],
                          "n": MEASURED[f]["n"]} for f in MEASURED},
            "settlement_marker": SETTLEMENT_PRICED}
