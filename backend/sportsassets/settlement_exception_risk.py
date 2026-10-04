"""SETTLEMENT-EXCEPTION RISK, MEASURED (R30C, program section 15).

THE GAP THIS CLOSES. The completed-game investment policy (agents/
paper_benchmark, CG V3) prices every ENTER on ORDINARY COMPLETION only:
`conditional_economics` is labelled CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED
and `exceptional_scenarios` writes "probability: UNMEASURED" beside every
postponement / suspension payoff. That was honest and it left the number the
owner asked for -- how often the exceptional states actually happen, and what
they cost against the completed-game assumption -- unmeasured. The R30A NFL
baseline made it concrete: the venue's postponement rule conflicts with the
book's void rule, and the venue pays $0.50 per contract on a tie that the
book's two-way probability does not price.

WHAT IS MEASURED, AND FROM WHAT. The venue's OWN settlements, as the outcome
join wrote them onto `external_valuations` (workers/ext_pinnacle_loop.
join_outcomes) and as the paper ledger settled held positions
(`paper_settlements`):

  ORDINARY          outcome_basis VENUE_SETTLEMENT_PRICE / VENUE_REPORTED_
                    OUTCOME: the venue paid one side in full
  DECLARED_VOID     outcome_basis CONFIRMED_VOID: the venue DECLARED a void
                    in a field the reader identified (`_declared_void`)
  SETTLED_AT_PRICE  outcome_basis NULL and a settlement_read strictly between
                    0 and 1 with no declared void: neither side was paid in
                    full. The join leaves these unresolved on purpose (a
                    nonbinary price is never read as a refund); here they are
                    COUNTED, because a game the venue settled at a price is
                    exactly the postponed / suspended state the completed-game
                    economics leave out. Production 2026-10-04 (research-sql
                    run 37226249974): aec-mlb-bal-nyy-2026-09-27 settled at
                    0.485 -- one of 65 settled MLB fixtures.
  TIE_AT_ONE_HALF   a 0.5 settlement where the graded interval CAN end level
                    and the book's probability is two-way (the NFL regular
                    season): the venue's tie payout

EVENT LEVEL. A postponement settles every market of its fixture, so a
fixture counts once (coalesce(event_key, slug)); repeated valuations of one
market are one market. A market whose reads disagree (a binary payout AND a
void) is CONFLICTING and excluded by name, never resolved by preference.

CELLS. (sport family, league, market type); the league is the venue slug's
league token (split_part(slug, '-', 2), the lane's own convention). A cell
with fewer than MIN_FIXTURES settled fixtures falls back, in order, to the
league pooled across market types, the family pooled, a CITED external base
rate (source, quote, count AND denominator, page hash), and finally an
UNMEASURED cell carrying a CONSERVATIVE PRIOR. UNKNOWN IS NEVER ZERO: the only
zero this module writes is a STRUCTURAL one (an MLB game, played to a result
in extra innings, cannot end level), and it says which rule makes it so. A
pooled level counts an event only over cells where the event can happen
(APPLICABLE or UNKNOWN): a cell where it is structurally impossible never
dilutes another league's rate.

EVERY ESTIMATE CARRIES AN UPPER BOUND: the larger of the Wilson score and the
exact Clopper-Pearson 95% bounds on k of n. The prior's upper bound is the
Clopper-Pearson bound of zero events in MIN_FIXTURES fixtures, or the largest
upper bound held for that state anywhere in the table (a small cell's bound
raised to its own observation included), whichever is larger -- an
unmeasured cell is never cheaper than a measured one.

THE COST PER DECISION (`decision_cost`). Against the completed-game
assumption (the held side pays $1 with probability p, else $0), an
exceptional state s with probability pi_s pays X_s per contract instead, so
the expected payout differs by pi_s x (p - X_s) per contract. X_s comes from
the decision's OWN venue rules text through `bettor_settlement_clauses` (a
tie stated 50-50 pays 50c; a cancellation stated as a refund pays the basis;
a last-fair-market-price settlement is VARIABLE in [0, 1]) and is the full
[0, 1] range where the text states nothing. The CONSERVATIVE cost takes, per
state, the worst payout and the end of the rate interval that makes the cost
largest (the upper bound when the difference is a loss, the lower bound when
it is a gain -- zero for a prior). The point estimate exists only where every
state has a measured rate and an exact payout; otherwise it is UNAVAILABLE
with the reason.

AUTHORITY: NONE. The cost is SHADOW EVIDENCE carried on the canonical decision
(canonical_components.settlement_exception_at_decision) for Eddie and Allie
in R30B. It does not change the ENTER / REFUSE rule, a threshold, a size, a
cap or an order. Pure but for the read in `measure` (plain SELECTs).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

VERSION = "SETTLEMENT_EXCEPTION_RISK_V1"
#: The entry experiment whose valuations the outcome join labels (the value
#: of bettor_external_shadow.EXPERIMENT_ID; a test pins equality, so this
#: module imports no collector module).
EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"
AUTHORITY = "SHADOW_EVIDENCE_ONLY_NOT_A_GATE"

# ── the program's named exceptional states (section 15) ─────────────────
P_POSTPONEMENT = "POSTPONEMENT"
P_ABANDONMENT_SUSPENSION = "ABANDONMENT_OR_SUSPENSION"
P_VOID = "VOID_OR_CANCELLATION"
P_TIE = "TIE_OR_DRAW_WHERE_THE_VENUE_AND_BOOK_PAYOUTS_DIFFER"
P_RULE_DIVERGENCE = "VENUE_VS_BOOK_RULE_DIVERGENCE"
PROGRAM_STATES = (P_POSTPONEMENT, P_ABANDONMENT_SUSPENSION, P_VOID, P_TIE,
                  P_RULE_DIVERGENCE)

# ── what a venue settlement record can distinguish ───────────────────────
#: The three cost-bearing events, mutually exclusive per fixture outcome.
E_PRICE = "NOT_COMPLETED_SETTLED_AT_A_PRICE"
E_VOID = "DECLARED_VOID"
E_TIE = "TIE_SETTLED_AT_ONE_HALF"
EVENTS = (E_PRICE, E_VOID, E_TIE)

#: THE PROGRAM'S STATES ON THE EVENTS THE RECORD CAN SEE. Postponement and
#: suspension are ONE measured event: the venue settles both at the last fair
#: market price ("delayed, postponed, or suspended and not rescheduled ...
#: within two weeks"), a settlement record does not say which happened, and
#: both pay the same thing -- so they share one rate and the cost counts it
#: ONCE. A postponement the venue reschedules inside its window settles on
#: the completed game: it delays capital and changes no payout.
PROGRAM_TO_EVENT = {
    P_POSTPONEMENT: E_PRICE,
    P_ABANDONMENT_SUSPENSION: E_PRICE,
    P_VOID: E_VOID,
    P_TIE: E_TIE,
}
JOINT_MEASUREMENT = {
    E_PRICE: ("POSTPONEMENT and ABANDONMENT_OR_SUSPENSION are measured "
              "JOINTLY: the venue settles both at the last fair market price "
              "and its settlement record does not distinguish them. Their "
              "split is UNMEASURED; the cost counts the joint event once"),
}

# market classes
C_ORDINARY = "ORDINARY_BINARY_SETTLEMENT"
C_CONFLICT = "CONFLICTING_SETTLEMENT_READS"
CLASS_OF_EVENT = {E_PRICE: E_PRICE, E_VOID: E_VOID, E_TIE: E_TIE}

#: The outcome join's bases (workers/ext_pinnacle_loop B_* and
#: agents/paper_xavier LABEL_BASES / VOID_BASIS; pinned equal by a test).
LABEL_BASES = ("VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME")
VOID_BASIS = "CONFIRMED_VOID"

#: Settled FIXTURES a cell's own rate is stated on; below it the cell falls
#: back (league -> family -> cited external -> prior). The same floor as the
#: pairing void rate (bettor_pair_observations.MIN_VOID_RATE_FIXTURES = 40,
#: pinned by a test): "a rate from a handful of fixtures is an anecdote with
#: a decimal point".
MIN_FIXTURES = 40
#: Two-sided 95%.
Z95 = 1.959963984540054
ALPHA = 0.05

# estimate statuses
S_MEASURED = "MEASURED"
S_POOLED_LEAGUE = "MEASURED_POOLED_ACROSS_MARKET_TYPES"
S_POOLED_FAMILY = "MEASURED_POOLED_ACROSS_THE_SPORT_FAMILY"
S_EXTERNAL = "EXTERNAL_CITED_BASE_RATE"
S_PRIOR = "UNMEASURED_CONSERVATIVE_PRIOR"
S_STRUCTURAL = "STRUCTURALLY_IMPOSSIBLE"
S_NOT_APPLICABLE = "NOT_APPLICABLE"
EVIDENCED = (S_MEASURED, S_POOLED_LEAGUE, S_POOLED_FAMILY, S_EXTERNAL)

# decision statuses
D_MEASURED = "MEASURED"
D_PRIOR = "PRIOR_BOUNDED"
D_UNAVAILABLE = "UNAVAILABLE"

R_NO_TABLE = "SETTLEMENT_EXCEPTION_TABLE_UNAVAILABLE"
R_NO_PROBABILITY = "NO_PROBABILITY_FOR_THE_HELD_SIDE"
R_NO_PRICE = "NO_ACQUISITION_PRICE"
R_NO_QTY = "NO_QUANTITY"
R_NO_FAMILY = "NO_SPORT_FAMILY"
R_TABLE_UNREADABLE = "SETTLEMENT_RECORDS_UNREADABLE"

#: Bounds on one read.
MAX_MARKETS = 20000
DIVERGENCE_WINDOW_DAYS = 45

_NUM = re.compile(r"^\s*[0-9]*\.?[0-9]+\s*$")


# ═════════════════════════════════════════════════════════════════════
# 1 · INTERVALS (pure)
# ═════════════════════════════════════════════════════════════════════

def wilson(k: int, n: int, z: float = Z95) -> tuple | None:
    """The Wilson score interval for k of n, or None when n is 0."""
    if n <= 0:
        return None
    p = k / float(n)
    den = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    lg = math.lgamma
    base = lg(n + 1)
    total = 0.0
    for i in range(0, k + 1):
        total += math.exp(base - lg(i + 1) - lg(n - i + 1) + i * lp
                          + (n - i) * lq)
    return min(1.0, total)


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> tuple | None:
    """The exact (Clopper-Pearson) two-sided interval for k of n, by
    bisection on the binomial CDF (no scipy in the image). None when n is 0."""
    if n <= 0:
        return None
    k = int(k)
    a2 = alpha / 2.0

    def solve(f, target):
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if f(mid) > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0
    # both CDFs fall as p rises, which is what `solve` assumes
    upper = 1.0 if k >= n else solve(lambda p: _binom_cdf(k, n, p), a2)
    # lower: P(X >= k) = alpha/2  <=>  P(X <= k-1) = 1 - alpha/2
    lower = 0.0 if k <= 0 else solve(lambda p: _binom_cdf(k - 1, n, p),
                                     1.0 - a2)
    return (max(0.0, lower), min(1.0, upper))


def interval(k: int, n: int) -> dict:
    """k of n with its point rate and the conservative 95% bounds: the
    larger upper / smaller lower of Wilson and Clopper-Pearson."""
    w, cp = wilson(k, n), clopper_pearson(k, n)
    if w is None:
        return {"k": int(k), "n": int(n), "rate": None, "lower_95": None,
                "upper_95": None, "wilson_95": None, "clopper_pearson_95": None}
    return {"k": int(k), "n": int(n), "rate": k / float(n),
            "lower_95": min(w[0], cp[0]), "upper_95": max(w[1], cp[1]),
            "wilson_95": [w[0], w[1]], "clopper_pearson_95": [cp[0], cp[1]],
            "bound_basis": ("the larger upper (smaller lower) of the Wilson "
                            "score and exact Clopper-Pearson 95% bounds")}


#: THE CONSERVATIVE PRIOR'S FLOOR: the exact 95% upper bound of ZERO events
#: in MIN_FIXTURES fixtures (1 - 0.025^(1/40) = 0.0880...). No evidence is
#: treated as no better than the least informative admissible measurement.
PRIOR_FLOOR_UPPER = clopper_pearson(0, MIN_FIXTURES)[1]


# ═════════════════════════════════════════════════════════════════════
# 2 · EXTERNAL BASE RATES: CITED OR REFUSED
# ═════════════════════════════════════════════════════════════════════
#
# A VALUE HERE IS A CLAIM ABOUT THE WORLD and it may enter only with its
# citation: the source, its URL, the retrieval instant, the VERBATIM quote
# each number was read from, the page revision / sha256, the reader that
# fetched it (GitHub Actions run), and the COUNT and DENOMINATOR -- a bare
# rate is refused (`admit_external`). The development container's egress
# policy denies these hosts, so the pages were read by the fetch-docs
# workflow (read-only, no secrets, no repository write) and the log of each
# run is the evidence.

CITATION_FIELDS = ("source", "source_url", "retrieved_at", "quote",
                   "page_sha256", "reader")

_NFL_TIE_QUOTE_2017_2024 = (
    "During these seasons, a total of 118 regular season games went to "
    "overtime, 7 of which ended in a tie. [table rows 5-7, the 2021-2022 "
    "ties:] November 14, 2021 Detroit Lions at Pittsburgh Steelers 16-16; "
    "September 11, 2022 Indianapolis Colts at Houston Texans 20-20; "
    "December 4, 2022 Washington Commanders at New York Giants 20-20")
_NFL_TIE_QUOTE_2025 = (
    "Through Week 3 of the 2026 season, a total of 17 regular season games "
    "went to overtime, 1 of which ended in a tie. [table row 1:] September "
    "28, 2025 Green Bay Packers at Dallas Cowboys 40-40")
_NFL_GAMES_QUOTE = (
    "It consists of 272 games, with each of the NFL's 32 teams playing 17 "
    "games during an 18-week period with one \"bye\" week off. ... The "
    "current formula has been in place since 2021, the last year that the "
    "NFL expanded its regular season.")

EXTERNAL_BASE_RATES: tuple = (
    {"sport_family": "football", "league": "nfl", "market": "h2h",
     "event": E_TIE, "count": 4, "denominator": 1360,
     "unit": "NFL REGULAR-SEASON games of the 2021-2025 seasons",
     "scope": ("REGULAR_SEASON: a playoff game is played to a winner, so "
               "this rate does not describe one"),
     "derivation": (
         "ties: 3 in the 2021-2024 seasons (the 2017-2024 section's rows "
         "dated 2021-11-14, 2022-09-11, 2022-12-04; none dated 2023 or "
         "2024) + 1 in the 2025 season (the 2025-present section's only "
         "row, 2025-09-28; that section counts one tie through week 3 of "
         "2026) = 4. games: 5 seasons x 272 scheduled games under the "
         "17-game formula in place since 2021 = 1360. A scheduled game not "
         "completed is not subtracted (not stated in the cited text); it "
         "would move the rate by under 0.1%% of itself"),
     "citations": (
         {"source": "Wikipedia, List of NFL tied games, section 2017-2024",
          "source_url": ("https://en.wikipedia.org/w/api.php?action=parse&"
                         "page=List_of_NFL_tied_games&prop=wikitext|revid&"
                         "section=7&format=json"),
          "retrieved_at": "2026-10-04T19:02:04Z",
          "page_revision": 1377928469,
          "page_sha256": ("1757078cc42be3a26a60038e3836a687357aa87c8c5d73"
                          "aaf1c72f8898f14d76"),
          "reader": ("fetch-docs run 37226668396 (job 111507716184), "
                     "github-actions runner"),
          "quote": _NFL_TIE_QUOTE_2017_2024},
         {"source": "Wikipedia, List of NFL tied games, section 2025-present",
          "source_url": ("https://en.wikipedia.org/w/api.php?action=parse&"
                         "page=List_of_NFL_tied_games&prop=wikitext|revid&"
                         "section=8&format=json"),
          "retrieved_at": "2026-10-04T19:01:36Z",
          "page_revision": 1377928469,
          "page_sha256": ("c273836bf3278034a6e3f240a86e2cd3df479b8ba2014f"
                          "41d014faf1f5fbbba2"),
          "reader": ("fetch-docs run 37226672249 (job 111507631758), "
                     "github-actions runner"),
          "quote": _NFL_TIE_QUOTE_2025},
         {"source": "Wikipedia, NFL regular season (lead section)",
          "source_url": ("https://en.wikipedia.org/w/api.php?action=parse&"
                         "page=National_Football_League_regular_season&"
                         "redirects=1&prop=wikitext|revid&section=0&"
                         "format=json"),
          "retrieved_at": "2026-10-04T19:01:44Z",
          "page_revision": 1356668022,
          "page_sha256": ("9aefb10b6af5f0b263723de75fbd769e828399b015dd6e"
                          "73e96cd1b8e26bc35f"),
          "reader": ("fetch-docs run 37226675669 (job 111507658826), "
                     "github-actions runner"),
          "quote": _NFL_GAMES_QUOTE},
     )},
)

R_EXT_NO_CITATION = "EXTERNAL_RATE_REJECTED_NO_CITATION"
R_EXT_COUNTS = "EXTERNAL_RATE_REJECTED_NO_COUNT_AND_DENOMINATOR"
R_EXT_EVENT = "EXTERNAL_RATE_REJECTED_UNDECLARED_EVENT"


def admit_external(entry: dict) -> dict:
    """An external base rate is admitted only with its citations, an integer
    count and denominator (0 <= count <= denominator, denominator > 0) and a
    declared event. Returns {ok, refusals}."""
    e = dict(entry or {})
    refusals = []
    cites = list(e.get("citations") or ())
    if not cites or any(
            not all(str((c or {}).get(f) or "").strip()
                    for f in CITATION_FIELDS) for c in cites):
        refusals.append(R_EXT_NO_CITATION)
    k, n = e.get("count"), e.get("denominator")
    if (not isinstance(k, int) or isinstance(k, bool) or not isinstance(n, int)
            or isinstance(n, bool) or n <= 0 or k < 0 or k > n):
        refusals.append(R_EXT_COUNTS)
    if e.get("event") not in EVENTS:
        refusals.append(R_EXT_EVENT)
    return {"ok": not refusals, "refusals": refusals}


def _external_for(family, league, market, event) -> dict | None:
    for e in EXTERNAL_BASE_RATES:
        if (e["sport_family"] == family and e["league"] in (league, "*")
                and e["market"] in (market, "*") and e["event"] == event
                and admit_external(e)["ok"]):
            return e
    return None


# ═════════════════════════════════════════════════════════════════════
# 3 · WHICH EVENTS APPLY (pure, rules of the game -- never a venue's terms)
# ═════════════════════════════════════════════════════════════════════

#: A league whose graded interval differs from its family's default. A
#: college football game is played to a result (overtime repeats), so it
#: cannot end level; the family default (football, OT_INCLUDED) can, through
#: the NFL's regular-season overtime. A rule of the game, like
#: bettor_venue_settlement.TIE_REACHABLE, which owns the family defaults.
#:
#: BASEBALL IS DECLARED PER LEAGUE (review, R30C). The family default
#: (baseball, OT_INCLUDED) -> cannot end level is true of MLB, whose games are
#: played to a result in extra innings (the venue's own MLB text: "Extra
#: innings are included if played"). It is NOT true of every league the
#: venue lists on the same winner type: KBO and NPB regular-season games end
#: level when the league's extra-inning cap is reached, and the venue lists
#: both (workers/ext_pinnacle_loop: "16 NPB and KBO events, on the SAME
#: winner type"). Applying the family default to them wrote a STRUCTURAL zero
#: that no rule supports. So MLB is declared unable to end level, KBO and NPB
#: able to, and any other baseball league is UNKNOWN (the prior), never a
#: structural zero by inheritance.
LEAGUE_TIE_REACHABLE = {("football", "cfb"): False,
                        ("baseball", "mlb"): False,
                        ("baseball", "kbo"): True,
                        ("baseball", "npb"): True}
#: Families whose structural tie answer is declared PER LEAGUE: a league of
#: these families missing from LEAGUE_TIE_REACHABLE is UNKNOWN, not the
#: family default.
LEAGUE_SCOPED_TIE_FAMILIES = frozenset({"baseball"})
#: The overtime treatment the venue's and the book's money lines share, per
#: family, where both are captured (bettor_venue_settlement.BOOK_SETTLEMENT
#: and the venue grading templates of paper_benchmark).
FAMILY_GRADED_OVERTIME = {"baseball": "OT_INCLUDED", "football": "OT_INCLUDED",
                          "soccer": "OT_EXCLUDED"}


def tie_applicability(family, league, market) -> dict:
    """Can a tie happen AND pay differently from the completed-game
    assumption? APPLICABLE / STRUCTURALLY_IMPOSSIBLE / NOT_APPLICABLE /
    UNKNOWN (treated as applicable at the prior: unknown is never zero)."""
    fam = str(family or "").strip().lower()
    lg = str(league or "").strip().lower()
    if str(market or "") != "h2h":
        return {"status": "UNKNOWN",
                "why": ("a line market can push; push handling on %r is not "
                        "modelled here" % market)}
    if fam == "soccer":
        return {"status": S_NOT_APPLICABLE,
                "why": ("the draw is a PRICED outcome on both sides: the book "
                        "de-vigs three ways (bettor_venue_settlement."
                        "BOOK_OUTCOMES soccer=3) and the venue's per-side "
                        "contract settles a draw as its own outcome, so the "
                        "completed-game probability already prices it")}
    from . import bettor_venue_settlement as V
    if (fam, lg) in LEAGUE_TIE_REACHABLE:
        reach = LEAGUE_TIE_REACHABLE[(fam, lg)]
        basis = "LEAGUE_TIE_REACHABLE[(%s, %s)]" % (fam, lg)
    elif fam in LEAGUE_SCOPED_TIE_FAMILIES:
        return {"status": "UNKNOWN",
                "basis": "LEAGUE_SCOPED_TIE_FAMILIES",
                "why": ("whether a %s/%s game can end level is declared per "
                        "league (extra-inning caps differ: KBO and NPB end "
                        "level, MLB does not) and %r is not declared"
                        % (fam, lg or "?", lg or "?"))}
    else:
        ot = FAMILY_GRADED_OVERTIME.get(fam)
        got = V.tie_is_reachable(sport_family=fam, overtime=ot)
        reach = got.get("permits_tie")
        basis = "bettor_venue_settlement.TIE_REACHABLE[(%s, %s)]" % (fam, ot)
    if reach is None:
        return {"status": "UNKNOWN",
                "why": "whether a %s/%s game can end level is not declared"
                       % (fam or "?", lg or "?")}
    if reach is False:
        return {"status": S_STRUCTURAL, "basis": basis,
                "why": ("the graded interval cannot end level (%s), so the "
                        "tie state is impossible by the rules of the game -- "
                        "a structural zero, not an unmeasured one" % basis)}
    if V.BOOK_OUTCOMES.get(fam) == 2:
        return {"status": "APPLICABLE", "basis": basis,
                "why": ("the game can end level and the book's money line "
                        "prices two outcomes, so a tie payout is outside the "
                        "completed-game probability")}
    return {"status": "UNKNOWN", "basis": basis,
            "why": "the book's outcome count for %s is not declared" % fam}


def event_applicability(event, family, league, market) -> dict:
    if event == E_TIE:
        return tie_applicability(family, league, market)
    return {"status": "APPLICABLE",
            "why": "any fixture can be postponed, suspended or voided"}


# ═════════════════════════════════════════════════════════════════════
# 4 · CLASSIFYING ONE MARKET'S SETTLEMENT (pure)
# ═════════════════════════════════════════════════════════════════════

def league_of(slug) -> str:
    """The venue slug's league token: split_part(slug, '-', 2)."""
    parts = str(slug or "").split("-")
    return parts[1].strip().lower() if len(parts) > 1 else ""


def _price(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v)
    if not _NUM.match(s):
        return None
    try:
        f = float(s.strip())
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def classify_market(*, bases=(), numeric_reads=(), tie_reachable=None,
                    paper_outcomes=(), paper_prices=()) -> dict:
    """One market's terminal class from its venue reads and the paper
    ledger's settlements of it. None when nothing terminal is recorded;
    CONFLICTING when the reads disagree (excluded, never resolved by
    preference). A 0.5 read is a TIE only where the interval can end level;
    otherwise (or unknown) it is a price settlement."""
    classes = set()
    bases = {str(b) for b in (bases or ()) if b}
    if bases & set(LABEL_BASES):
        classes.add(C_ORDINARY)
    if VOID_BASIS in bases:
        classes.add(E_VOID)
    for r in numeric_reads or ():
        sp = _price(r)
        if sp is not None and 0.0 < sp < 1.0:
            classes.add(E_TIE if (sp == 0.5 and tie_reachable is True)
                        else E_PRICE)
    for o in paper_outcomes or ():
        if o in ("WON", "LOST"):
            classes.add(C_ORDINARY)
        elif o == "VOID_REFUND":
            classes.add(E_VOID)
    for sp in paper_prices or ():
        f = _price(sp)
        if f is not None and 0.0 < f < 1.0:
            classes.add(E_TIE if (f == 0.5 and tie_reachable is True)
                        else E_PRICE)
    if not classes:
        return {"class": None}
    if len(classes) > 1:
        return {"class": C_CONFLICT, "classes": sorted(classes)}
    return {"class": classes.pop()}


# ═════════════════════════════════════════════════════════════════════
# 5 · THE TABLE (pure)
# ═════════════════════════════════════════════════════════════════════

def _cell_key(fam, lg, mkt) -> str:
    return "%s/%s/%s" % (fam or "?", lg or "?", mkt or "?")


def build_table(markets: list, divergence: list | None = None, *,
                as_of: float | None = None, source: dict | None = None
                ) -> dict:
    """THE MEASURED EXCEPTION-RISK TABLE from per-market rows.

    `markets`: [{slug, sport_family, market, fixture, bases, numeric_reads,
    paper_outcomes, paper_prices}] -- one row per venue market.
    `divergence`: [{slug, sport_family, market, per_condition}] -- the
    latest recorded per-condition settlement comparison per market."""
    counts: dict = {}          # (fam, lg, mkt) -> {fixtures, events, conflict}
    excluded: dict = {}
    for m in markets or ():
        fam = str(m.get("sport_family") or "").strip().lower() or "unknown"
        lg = league_of(m.get("slug"))
        mkt = str(m.get("market") or "").strip().lower() or "unknown"
        reach = tie_applicability(fam, lg, mkt).get("status") == "APPLICABLE"
        got = classify_market(bases=m.get("bases") or (),
                              numeric_reads=m.get("numeric_reads") or (),
                              tie_reachable=reach,
                              paper_outcomes=m.get("paper_outcomes") or (),
                              paper_prices=m.get("paper_prices") or ())
        cls = got["class"]
        if cls is None:
            excluded["NOT_TERMINAL"] = excluded.get("NOT_TERMINAL", 0) + 1
            continue
        fx = str(m.get("fixture") or m.get("slug"))
        # A POOL COUNTS AN EVENT ONLY WHERE THE EVENT CAN HAPPEN (review,
        # R30C). Pooling every fixture of a family into every event's
        # denominator let college-football fixtures -- which cannot end level
        # -- dilute the NFL tie rate: 45 cfb fixtures turned the cited NFL
        # base rate (4 of 1,360) into "0 of 46, MEASURED", and 2,000 drove
        # the bound below the NFL's own point rate. A fixture enters an
        # event's denominator only from a market whose cell is APPLICABLE or
        # UNKNOWN for that event; STRUCTURAL and NOT_APPLICABLE cells are out
        # of numerator and denominator alike.
        can = {e for e in EVENTS
               if event_applicability(e, fam, lg, mkt)["status"]
               not in (S_STRUCTURAL, S_NOT_APPLICABLE)}
        for key in ((fam, lg, mkt), (fam, lg, "*"), (fam, "*", "*")):
            c = counts.setdefault(key, {"fixtures": set(), "conflict": set(),
                                        "events": {e: set() for e in EVENTS},
                                        "denominators": {e: set()
                                                         for e in EVENTS},
                                        "markets": 0})
            c["markets"] += 1
            if cls == C_CONFLICT:
                c["conflict"].add(fx)
                continue
            c["fixtures"].add(fx)
            for e in can:
                c["denominators"][e].add(fx)
            if cls in CLASS_OF_EVENT and cls in can:
                c["events"][cls].add(fx)
        if cls == C_CONFLICT:
            excluded[C_CONFLICT] = excluded.get(C_CONFLICT, 0) + 1
    # a fixture with a conflicting market is out of that cell entirely
    for c in counts.values():
        c["fixtures"] -= c["conflict"]
        for e in EVENTS:
            c["events"][e] -= c["conflict"]
            c["denominators"][e] -= c["conflict"]

    def obs(key, e):
        c = counts.get(key)
        if not c:
            return interval(0, 0)
        return interval(len(c["events"][e]), len(c["denominators"][e]))

    div = rule_divergence(divergence or [])
    cells = []
    keys = sorted({k for k in counts if k[2] != "*" and k[1] != "*"}
                  | {(d["sport_family"], d["league"], d["market"])
                     for d in div.values()})
    first: dict = {}
    for key in keys:
        fam, lg, mkt = key
        row = {"cell": _cell_key(*key), "sport_family": fam, "league": lg,
               "market": mkt,
               "settled_fixtures": len((counts.get(key) or {}).get(
                   "fixtures") or ()),
               "conflicting_fixtures": len((counts.get(key) or {}).get(
                   "conflict") or ()),
               "events": {}}
        for e in EVENTS:
            app = event_applicability(e, fam, lg, mkt)
            internal = obs(key, e)
            if app["status"] in (S_STRUCTURAL, S_NOT_APPLICABLE):
                est = {"status": app["status"], "rate": 0.0,
                       "lower_95": 0.0, "upper_95": 0.0, "why": app["why"]}
            else:
                est = None
                for lvl, k2, st in ((0, key, S_MEASURED),
                                    (1, (fam, lg, "*"), S_POOLED_LEAGUE),
                                    (2, (fam, "*", "*"), S_POOLED_FAMILY)):
                    o = obs(k2, e)
                    if o["n"] >= MIN_FIXTURES:
                        est = dict(o, status=st, level=k2[1:] if lvl else None,
                                   basis=("%d of %d settled fixtures (%s)"
                                          % (o["k"], o["n"], _cell_key(*k2))))
                        break
                if est is None:
                    ext = _external_for(fam, lg, mkt, e)
                    if ext is not None:
                        iv = interval(ext["count"], ext["denominator"])
                        est = dict(iv, status=S_EXTERNAL,
                                   basis=("%d of %d %s" % (
                                       ext["count"], ext["denominator"],
                                       ext["unit"])),
                                   scope=ext.get("scope"),
                                   derivation=ext.get("derivation"),
                                   citations=[dict(c) for c in
                                              ext["citations"]])
                if est is None:
                    est = {"status": S_PRIOR, "rate": None, "lower_95": 0.0,
                           "upper_95": None,
                           "why": ("%d settled fixture(s) in the cell and "
                                   "fewer than %d at every pooled level; no "
                                   "cited external base rate; UNKNOWN IS NOT "
                                   "ZERO" % (internal["n"], MIN_FIXTURES))}
                if app["status"] == "UNKNOWN":
                    est["applicability_unknown"] = app["why"]
            est["internal_observation"] = internal
            if e in JOINT_MEASUREMENT:
                est["joint"] = JOINT_MEASUREMENT[e]
            row["events"][e] = est
        d = div.get(row["cell"])
        row["rule_divergence"] = d or {
            "status": "NOT_RECORDED",
            "why": "no per-condition settlement comparison recorded for the "
                   "cell in the window"}
        cells.append(row)
        first[row["cell"]] = row
    # SMALL-SAMPLE EVIDENCE IS NOT DISCARDED BY A FALLBACK: a cell whose own
    # observed rate exceeds the bound it fell back to carries its own exact
    # upper bound instead. Applied to the EVIDENCED fallbacks FIRST (review,
    # R30C): the prior was computed before this raise, so an unmeasured cell
    # could carry a bound below one the table held elsewhere -- contrary to
    # the invariant this module states.
    def raise_to_own(est):
        io = est.get("internal_observation") or {}
        if (est["status"] not in (S_MEASURED, S_STRUCTURAL, S_NOT_APPLICABLE)
                and io.get("rate") is not None
                and est.get("upper_95") is not None
                and io["rate"] > est["upper_95"]):
            est["upper_95"] = io["upper_95"]
            est["raised_by_internal_observation"] = True
    for c in cells:
        for e in EVENTS:
            if c["events"][e]["status"] in EVIDENCED:
                raise_to_own(c["events"][e])
    # THE PRIOR, ONCE EVERY EVIDENCED ESTIMATE IS FINAL (raised bounds
    # included): never cheaper than the floor or than any evidenced cell's
    # upper bound for the same event.
    prior_upper = {}
    for e in EVENTS:
        ups = [c["events"][e]["upper_95"] for c in cells
               if c["events"][e]["status"] in EVIDENCED
               and c["events"][e].get("upper_95") is not None]
        prior_upper[e] = max([PRIOR_FLOOR_UPPER] + ups)
    for c in cells:
        for e in EVENTS:
            est = c["events"][e]
            if est["status"] == S_PRIOR:
                est["upper_95"] = prior_upper[e]
                est["prior_basis"] = (
                    "max(Clopper-Pearson 95%% upper bound of 0 events in %d "
                    "fixtures = %.4f, the largest evidenced upper bound for "
                    "this event in the table, small-sample raises included)"
                    % (MIN_FIXTURES, PRIOR_FLOOR_UPPER))
                raise_to_own(est)
    out = {"version": VERSION, "as_of": as_of, "min_fixtures": MIN_FIXTURES,
           "prior_floor_upper_95": PRIOR_FLOOR_UPPER,
           "prior_upper_95": prior_upper, "cells": cells,
           "pooled": {_cell_key(*k): {
               "settled_fixtures": len(v["fixtures"]),
               "events": {e: interval(len(v["events"][e]),
                                      len(v["denominators"][e]))
                          for e in EVENTS},
               "pooled_over": ("per event, only fixtures of cells where the "
                               "event is APPLICABLE or UNKNOWN (a structural "
                               "or not-applicable cell is in neither the "
                               "numerator nor the denominator)")}
               for k, v in sorted(counts.items()) if "*" in k},
           "excluded": excluded,
           "external_base_rates": [
               dict({k: e[k] for k in ("sport_family", "league", "market",
                                       "event", "count", "denominator",
                                       "unit")},
                    admitted=admit_external(e)) for e in EXTERNAL_BASE_RATES],
           "program_states": {p: PROGRAM_TO_EVENT.get(p) for p in
                              PROGRAM_STATES if p != P_RULE_DIVERGENCE},
           "rule_divergence_is": (
               "a property of the RULES, per condition, not an event: where "
               "the venue's and the book's payouts for one condition differ, "
               "the book's probability does not price the venue's contract in "
               "that state. Its cost is carried by the event the condition "
               "describes (the venue's payout is what `decision_cost` uses), "
               "so it is reported here and never added a second time"),
           "source": dict(source or {}),
           "authority": AUTHORITY}
    out["table_sha256"] = hashlib.sha256(json.dumps(
        {"cells": cells, "prior": prior_upper}, sort_keys=True,
        default=str).encode()).hexdigest()
    return out


def rule_divergence(rows: list) -> dict:
    """Per cell: of the markets whose settlement comparison was recorded,
    how many show a MISMATCH on each exceptional condition, with the payout
    pairs and an interval on the share with any mismatch."""
    from . import bettor_settlement_terms as ST
    ordinary = (ST.C_FULL, ST.C_OVERTIME)
    cells: dict = {}
    for r in rows or ():
        fam = str(r.get("sport_family") or "").strip().lower() or "unknown"
        lg = league_of(r.get("slug"))
        mkt = str(r.get("market") or "").strip().lower() or "unknown"
        pc = r.get("per_condition")
        if isinstance(pc, str):
            try:
                pc = json.loads(pc)
            except ValueError:
                pc = None
        if not isinstance(pc, dict):
            continue
        c = cells.setdefault(_cell_key(fam, lg, mkt), {
            "sport_family": fam, "league": lg, "market": mkt,
            "markets": set(), "any_mismatch": set(), "per_condition": {}})
        slug = str(r.get("slug"))
        c["markets"].add(slug)
        for cond, v in pc.items():
            if cond in ordinary or not isinstance(v, dict):
                continue
            pcd = c["per_condition"].setdefault(cond, {
                "verdicts": {}, "mismatch_payouts": {}})
            verdict = str(v.get("verdict") or "UNRECORDED")
            pcd["verdicts"][verdict] = pcd["verdicts"].get(verdict, 0) + 1
            if verdict == ST.V_MISMATCH:
                c["any_mismatch"].add(slug)
                pair = "book=%s;venue=%s" % (v.get("book_payout"),
                                             v.get("venue_payout"))
                pcd["mismatch_payouts"][pair] = \
                    pcd["mismatch_payouts"].get(pair, 0) + 1
    out = {}
    for key, c in cells.items():
        n, k = len(c["markets"]), len(c["any_mismatch"])
        out[key] = {"sport_family": c["sport_family"], "league": c["league"],
                    "market": c["market"], "status": "MEASURED",
                    "markets_compared": n,
                    "markets_with_an_exceptional_mismatch": k,
                    "share_with_a_mismatch": interval(k, n),
                    "per_condition": c["per_condition"]}
    return out


# ═════════════════════════════════════════════════════════════════════
# 6 · THE COST OF ONE DECISION (pure)
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def venue_payouts(venue_rules_text, *, holding_side: str, price: float
                  ) -> dict:
    """Per event, the held side's payout per contract as [min, max] in
    dollars, from the decision's OWN venue rules text
    (bettor_settlement_clauses). Unstated or variable is the full [0, 1]."""
    from . import bettor_settlement_clauses as SC
    side = ("ORDER_INTENT_BUY_LONG" if holding_side == "LONG"
            else "ORDER_INTENT_BUY_SHORT")
    text = str(venue_rules_text or "")
    out = {}
    # PRICE SETTLEMENT: by definition a price in (0, 1) that nobody knows in
    # advance -- the venue's "last fair market price". The rule is read by
    # the condition -> payout reader (bettor_settlement_terms), which reads
    # the venue's "delayed, postponed, or suspended and not rescheduled ...
    # last fair market price" sentence; the clause grammar refuses that
    # sentence on its "not" (its negation guard), and that refusal is kept
    # beside it. Either way the payout is the [0, 1] range.
    from . import bettor_settlement_terms as ST
    read = ST.read_terms(text)
    rule = (read.get("terms") or {}).get(ST.C_NOT_PLAYED)
    post = SC.read_outcome(text, SC.POSTPONED)
    out[E_PRICE] = {"payout_range": [0.0, 1.0],
                    "venue_rule": rule,
                    "venue_rule_reader": ("bettor_settlement_terms.read_terms"
                                          "[%s]" % ST.C_NOT_PLAYED),
                    "clause_reader": post.get("resolution") or
                    post.get("refusal"),
                    "basis": ("a settlement at a price strictly between 0 "
                              "and 1, known only when the venue states it "
                              "(the last fair market price): worst case 0")}
    for ev, outcome in ((E_VOID, SC.CANCELLED), (E_TIE, SC.TIE)):
        r = SC.read_outcome(text, outcome)
        res = r.get("resolution")
        got = SC.side_payout_cents(res, side,
                                   basis_cents=int(round(price * 100)))
        if got.get("cents") is not None:
            usd = got["cents"] / 100.0
            rng, basis = [usd, usd], ("the venue's own text: %s"
                                      % (r.get("clause") or res))
        else:
            rng = [0.0, 1.0]
            basis = ("the venue's text establishes no fixed payout for this "
                     "state (%s): the full [0, 1] range"
                     % (got.get("refusal") or r.get("refusal") or "UNSTATED"))
        out[ev] = {"payout_range": rng, "venue_rule": res,
                   "venue_clause": r.get("clause"), "basis": basis}
    return out


def _bound(lo, hi, d):
    """max over pi in [lo, hi] of pi x d."""
    if d >= 0:
        return (hi or 0.0) * d
    return (lo or 0.0) * d


def find_cell(table: dict, *, sport_family, league, market) -> dict | None:
    key = _cell_key(str(sport_family or "").strip().lower(),
                    str(league or "").strip().lower(),
                    str(market or "").strip().lower())
    for c in (table or {}).get("cells") or ():
        if c["cell"] == key:
            return c
    return None


def cell_for(table: dict, *, sport_family, league, market) -> dict:
    """The table's cell, or -- for a cell with no settled record at all --
    one built now on the same fallbacks (pooled, external, prior)."""
    got = find_cell(table, sport_family=sport_family, league=league,
                    market=market)
    if got is not None:
        return got
    fam = str(sport_family or "").strip().lower()
    lg = str(league or "").strip().lower()
    mkt = str(market or "").strip().lower()
    pooled = (table or {}).get("pooled") or {}
    prior = (table or {}).get("prior_upper_95") or {}
    row = {"cell": _cell_key(fam, lg, mkt), "sport_family": fam,
           "league": lg, "market": mkt, "settled_fixtures": 0,
           "events": {}, "built_at_decision": True}
    for e in EVENTS:
        app = event_applicability(e, fam, lg, mkt)
        if app["status"] in (S_STRUCTURAL, S_NOT_APPLICABLE):
            row["events"][e] = {"status": app["status"], "rate": 0.0,
                                "lower_95": 0.0, "upper_95": 0.0,
                                "why": app["why"]}
            continue
        est = None
        for k2, st in (((fam, lg, "*"), S_POOLED_LEAGUE),
                       ((fam, "*", "*"), S_POOLED_FAMILY)):
            p = (pooled.get(_cell_key(*k2)) or {}).get("events", {}).get(e)
            if p and p.get("n", 0) >= MIN_FIXTURES:
                est = dict(p, status=st)
                break
        if est is None:
            ext = _external_for(fam, lg, mkt, e)
            if ext is not None:
                est = dict(interval(ext["count"], ext["denominator"]),
                           status=S_EXTERNAL,
                           citations=[dict(c) for c in ext["citations"]])
        if est is None:
            est = {"status": S_PRIOR, "rate": None, "lower_95": 0.0,
                   "upper_95": prior.get(e, PRIOR_FLOOR_UPPER)}
        row["events"][e] = est
    return row


def decision_cost(table: dict | None, *, sport_family, league, market,
                  holding_side, p, price, qty, venue_rules_text=None,
                  conditional_net_usd=None) -> dict:
    """THE EXPECTED EXCEPTION COST OF ONE DECISION against the completed-game
    assumption: per contract sum_s pi_s x (p - X_s), at the conservative end
    of each rate interval and payout range, times the quantity. Never raises;
    UNAVAILABLE with the reason when an input is missing."""
    out: dict[str, Any] = {"version": VERSION, "authority": AUTHORITY,
                           "gates_the_decision": False}
    if not table or not table.get("cells") and not table.get("pooled") \
            and table.get("prior_upper_95") is None:
        return dict(out, status=D_UNAVAILABLE, why=R_NO_TABLE)
    pp, c, q = _num(p), _num(price), _num(qty)
    if pp is None or not (0.0 <= pp <= 1.0):
        return dict(out, status=D_UNAVAILABLE, why=R_NO_PROBABILITY)
    if c is None or not (0.0 < c < 1.0):
        return dict(out, status=D_UNAVAILABLE, why=R_NO_PRICE)
    if q is None or q <= 0:
        return dict(out, status=D_UNAVAILABLE, why=R_NO_QTY)
    if not sport_family:
        return dict(out, status=D_UNAVAILABLE, why=R_NO_FAMILY)
    if holding_side not in ("LONG", "SHORT"):
        return dict(out, status=D_UNAVAILABLE,
                    why="HOLDING_SIDE_NOT_LONG_OR_SHORT")
    cell = cell_for(table, sport_family=sport_family, league=league,
                    market=market)
    pays = venue_payouts(venue_rules_text, holding_side=holding_side, price=c)
    per_event, upper, point, point_why, priors = {}, 0.0, 0.0, [], []
    for e in EVENTS:
        est = cell["events"][e]
        lo, hi = est.get("lower_95"), est.get("upper_95")
        x_min, x_max = pays[e]["payout_range"]
        d_worst, d_best = pp - x_min, pp - x_max
        cost_up = _bound(lo, hi, d_worst)
        exact = x_min == x_max
        cost_pt = None
        if est["status"] in (S_STRUCTURAL, S_NOT_APPLICABLE):
            cost_pt = 0.0
        elif est["status"] in EVIDENCED and exact and \
                est.get("rate") is not None:
            cost_pt = est["rate"] * (pp - x_min)
        else:
            point_why.append("%s: %s" % (
                e, "the rate is a prior" if est["status"] == S_PRIOR
                else "the payout is a range [%.2f, %.2f], not a value"
                % (x_min, x_max)))
        if est["status"] == S_PRIOR:
            priors.append(e)
        upper += cost_up
        if cost_pt is not None:
            point += cost_pt
        per_event[e] = {
            "rate_status": est["status"], "rate": est.get("rate"),
            "rate_lower_95": lo, "rate_upper_95": hi,
            "payout_per_contract_range": [x_min, x_max],
            "payout_basis": pays[e]["basis"],
            "venue_rule": pays[e].get("venue_rule"),
            "payout_difference_per_contract_range": [d_best, d_worst],
            "cost_per_contract_conservative": cost_up,
            "cost_per_contract_point": cost_pt}
    status = D_PRIOR if priors else D_MEASURED
    cond = _num(conditional_net_usd)
    out.update(
        status=status,
        why=(None if status == D_MEASURED else
             "evidenced rates for every event but %s, which carry the "
             "conservative prior" % ", ".join(priors)),
        cell=cell["cell"], cell_built_at_decision=bool(
            cell.get("built_at_decision")),
        probability_held_side_pays=pp, price=c, qty=q,
        events=per_event,
        expected_exception_cost_per_contract_conservative=upper,
        expected_exception_cost_usd_conservative=upper * q,
        expected_exception_cost_usd_point=(point * q if not point_why
                                           else None),
        point_unavailable_because=point_why or None,
        conditional_net_usd=cond,
        conditional_net_less_conservative_exception_cost_usd=(
            None if cond is None else cond - upper * q),
        table_sha256=table.get("table_sha256"), table_as_of=table.get("as_of"),
        method=("sum over mutually exclusive exceptional events of "
                "pi x (p - X) per contract; conservative = worst payout and "
                "the rate bound that maximises the cost"),
        not_claimed=("the ENTER/REFUSE rule, size and caps are unchanged: this "
                     "is shadow evidence for Eddie / Allie (R30B)"))
    return out


# ═════════════════════════════════════════════════════════════════════
# 7 · THE READ (plain SELECTs)
# ═════════════════════════════════════════════════════════════════════

MARKETS_SQL = """
    SELECT us_market_slug AS slug,
           min(sport_family) AS sport_family, min(market) AS market,
           min(coalesce(event_key, us_market_slug)) AS fixture,
           array_agg(DISTINCT outcome_basis)
               FILTER (WHERE outcome_basis IS NOT NULL) AS bases,
           array_agg(DISTINCT trim(settlement_read))
               FILTER (WHERE outcome_basis IS NULL
                         AND settlement_read ~ '^\\s*[0-9]*\\.?[0-9]+\\s*$')
               AS numeric_reads,
           max(settlement_read_at) AS last_read_at
      FROM external_valuations
     WHERE experiment_id = $1 AND us_market_slug IS NOT NULL
       AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
     GROUP BY us_market_slug
     LIMIT $2
"""

PAPER_SQL = """
    WITH s AS (
      SELECT DISTINCT ON (position_key) position_key, us_market_slug,
             outcome, payout_per_contract, holding_side,
             evidence->>'venue_long_price' AS venue_long_price
        FROM paper_settlements ORDER BY position_key, version DESC)
    SELECT s.*, v.sport_family, v.market, v.fixture
      FROM s LEFT JOIN LATERAL (
        SELECT sport_family, market,
               coalesce(event_key, us_market_slug) AS fixture
          FROM external_valuations x
         WHERE x.us_market_slug = s.us_market_slug
         ORDER BY x.id DESC LIMIT 1) v ON true
     LIMIT $1
"""

DIVERGENCE_SQL = """
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           sport_family, market,
           settlement_comparison->'per_condition' AS per_condition
      FROM external_valuations
     WHERE experiment_id = $1 AND us_market_slug IS NOT NULL
       AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
       AND decided_at > to_timestamp($2) - make_interval(days => $3)
     ORDER BY us_market_slug, decided_at DESC
     LIMIT $4
"""


async def measure(conn, *, now: float | None = None) -> dict:
    """The table from the venue's own settlement records and the paper
    ledger's settlements. Never raises: an unreadable record is the table's
    named refusal, never an empty table that reads as 'no exceptions'."""
    import time as _t
    at = float(now if now is not None else _t.time())
    try:
        rows = [dict(r) for r in await conn.fetch(MARKETS_SQL, EXPERIMENT_ID,
                                                  MAX_MARKETS)]
        paper = [dict(r) for r in await conn.fetch(PAPER_SQL, MAX_MARKETS)]
        div = [dict(r) for r in await conn.fetch(
            DIVERGENCE_SQL, EXPERIMENT_ID, at, DIVERGENCE_WINDOW_DAYS,
            MAX_MARKETS)]
    except Exception as exc:                                    # noqa: BLE001
        return {"version": VERSION, "ok": False, "refusal": R_TABLE_UNREADABLE,
                "error": type(exc).__name__, "as_of": at,
                "authority": AUTHORITY}
    by_slug = {r["slug"]: dict(r, bases=list(r.get("bases") or ()),
                               numeric_reads=list(r.get("numeric_reads")
                                                  or ()),
                               paper_outcomes=[], paper_prices=[])
               for r in rows}
    for p in paper:
        slug = p["us_market_slug"]
        m = by_slug.setdefault(slug, {
            "slug": slug, "sport_family": p.get("sport_family"),
            "market": p.get("market"), "fixture": p.get("fixture") or slug,
            "bases": [], "numeric_reads": [], "paper_outcomes": [],
            "paper_prices": []})
        if p["outcome"] == "SETTLED_AT_VENUE_PRICE":
            m["paper_prices"].append(p.get("venue_long_price")
                                     or p.get("payout_per_contract"))
        else:
            m["paper_outcomes"].append(p["outcome"])
    for r in div:
        if isinstance(r.get("per_condition"), str):
            try:
                r["per_condition"] = json.loads(r["per_condition"])
            except ValueError:
                r["per_condition"] = None
    t = build_table(list(by_slug.values()), div, as_of=at, source={
        "venue_settlement_markets": len(rows),
        "paper_settled_positions": len(paper),
        "markets_with_a_recorded_comparison": len(div),
        "records": ("external_valuations (outcome join: outcome_basis, "
                    "settlement_read) and paper_settlements (latest "
                    "version per position)"),
        "divergence_window_days": DIVERGENCE_WINDOW_DAYS})
    return dict(t, ok=True, refusal=None)


def describe() -> dict:
    return {"version": VERSION, "events": list(EVENTS),
            "program_states": dict(PROGRAM_TO_EVENT),
            "joint_measurement": dict(JOINT_MEASUREMENT),
            "min_fixtures": MIN_FIXTURES,
            "prior_floor_upper_95": PRIOR_FLOOR_UPPER,
            "fallback_order": [S_MEASURED, S_POOLED_LEAGUE, S_POOLED_FAMILY,
                               S_EXTERNAL, S_PRIOR],
            "unknown_is_never_zero": True,
            "structural_zero_only_by_a_rule_of_the_game": True,
            "authority": AUTHORITY}
