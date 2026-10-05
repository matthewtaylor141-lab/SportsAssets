"""NFL MONEY LINE: THE VENUE CONTRACT AND PINNACLE'S BET, COMPARED AS PAYOUTS.

WHY THIS EXISTS (R30A, the NFL P0). Every NFL market was refused: the
completed-game policy found no completed-game terms for football, football
was outside the de-vig set, and the strict policy refused settlement. The
owner's instruction was to repair the integration, not to add football to an
allowlist -- and where external evidence is genuinely insufficient, to keep
the refusal and name what is missing. This module is the integration: every
settlement state of an NFL game, what EACH side pays in it (as a number or a
named payout class, never as shared wording), the citation each payout was
read from, and the one state where the two sides pay differently in an
ordinarily completed game -- the TIE -- priced from cited evidence instead of
assumed away.

PURE. Standard library only (math, re, datetime, zoneinfo). Nothing here
imports a paper, live, funded or execution module, and nothing here decides
an order: callers (the completed-game policy, Xavier's measure) read it.

──────────────────────────────────────────────────────────────────────────
THE PAYOUTS, STATE BY STATE (per $1 contract on the side held)

  state                      venue (Polymarket US listing)   Pinnacle 2-way ML
  regulation win             1 / 0 on the winner             wins / loses
  overtime win               1 / 0 ("Overtime is included    wins / loses ("Game
                             if played.")                    ... include points
                                                             scored in overtime.")
  TIE after overtime         0.50 to EACH side ("If the      VOID, stake returned
                             game ends in a tie, the market  ("If a draw is not
                             will settle to $0.50.")         offered and a draw
                                                             happens, then bets on
                                                             both teams will be
                                                             voided.")
  postponed, rescheduled     graded on the rescheduled game  VOID if not started
    inside two weeks         (the price clause fires only    within 12 h of the
                             when NOT rescheduled in two     original time
                             weeks; not itself a stated
                             payout)
  postponed / suspended, not last fair market price S,       VOID (not started in
    rescheduled in two weeks  S in [0, 1]                    12 h; or suspended
                                                             < 55 min and not
                                                             completed in 12 h) or
                                                             the score at the
                                                             suspension (>= 55 min
                                                             and not resumed in
                                                             12 h)
  Pro Bowl / exhibition /    --                              its own action rule:
    preseason / postseason                                   NEVER TRADED here: a
                                                             game is traded only
                                                             when it is ESTABLISHED
                                                             as regular season
                                                             (cited season window)

THE TIE IS AN ORDINARY OUTCOME, NOT AN EXCEPTION. A regular-season game that
is level after its one overtime period simply ends tied: it is a completed
game, both sides settle it, and they settle it DIFFERENTLY. Because the book
voids a tie, its de-vigged two-way price is a probability CONDITIONAL ON NO
TIE. The venue contract pays 1 on a win, 0.5 on a tie and 0 on a loss, so the
held side's expected payout is

    p_venue = (1 - t) * p_book + 0.5 * t

with t the tie probability. t is not assumed: it is the cited empirical NFL
tie frequency under the CURRENT overtime rule, as an exact (Clopper-Pearson)
interval, and p_venue is evaluated at the end of that interval that is WORSE
for the side being bought -- the highest t for a side whose conditional
chance exceeds one half (a tie pays it less than it expected), the lowest t
for the other side (a tie pays it more). Never the favourable end.

POSTPONEMENT, SUSPENSION AND ABANDONMENT ARE EXCEPTIONAL. The completed-game
economics are conditional on the game being completed; these states pay
differently on the two sides (above) and their frequency is NOT measured
here. They are carried as disclosed exceptional-settlement risk, payoffs
stated, probabilities UNMEASURED -- never set to zero and never folded into
the completed-game EV. For the STRICT policy (every condition must pay the
same on both sides) they remain a refusal, named in `STRICT_POLICY_MISSING`.
"""
from __future__ import annotations

import math
import re

VERSION = "BETTOR_NFL_SETTLEMENT_V1"

# ═════════════════════════════════════════════════════════════════════
# THE CITATIONS (retrieved, not recalled)
# ═════════════════════════════════════════════════════════════════════

PINNACLE_RULES_URL = "https://www.pinnacle.com/en/future/betting-rules"
PINNACLE_PAGE_SHA256 = ("63d6432114be131dfbab98baf91f8777a98549221a59c288fa"
                        "76916c3d8303fd")
#: Two reads of the same page by the GitHub runner (fetch-docs). The second,
#: for this stream, is byte-identical (same size, same sha256), and its
#: American Football MARKET Rules list props only (Team to Score Next,
#: Passing Statistics, ...): there is no market rule for the money line, so
#: by the publisher's own precedence the sport rules and then the General
#: Rules govern it.
PINNACLE_CAPTURES = (
    {"retrieved_at": "2026-10-03T23:12:56Z", "run_id": 37161033936,
     "job_id": 111314325662, "bytes": 119592, "sha256": PINNACLE_PAGE_SHA256,
     "held_in": "tests/fixtures/pinnacle_american_football_rules_2026_10_03."
                "json (american_football_section, general_rules_section)"},
    {"retrieved_at": "2026-10-04T18:50:51Z", "run_id": 37225987459,
     "job_id": 111505635324, "bytes": 119592, "sha256": PINNACLE_PAGE_SHA256,
     "held_in": "this module (R30A re-read; unchanged)"},
)

Q_BOOK_TIE = ("If a “Draw” price is offered in a Money Line market, and "
              "the draw happens, then bets on each team lose. If a draw is "
              "not offered and a draw happens, then bets on both teams will "
              "be voided.")
Q_BOOK_OVERTIME = ("Bets on the Game and 2nd Half-periods include points "
                   "scored in overtime.")
Q_BOOK_NOT_STARTED = ("If a game is not started within 12 hours of its "
                      "originally scheduled time all bets will be voided.")
Q_BOOK_SUSPENDED = (
    "If a game is suspended with fewer than 55 minutes completed and is not "
    "completed within 12 hours of suspension, all bets on the Game-period "
    "will be voided and bets on completed periods will have action. If a "
    "game is suspended after 55 minutes of play and not resumed within 12 "
    "hours of suspension, then regardless of whether the game is completed "
    "at a later date or not, all bets will have action and the score when "
    "the game was suspended will be considered final. In addition, if the "
    "cumulative playing time of all quarters is less than 55 minutes, "
    "Full-game markets will have no action and bets will be deemed void.")
Q_BOOK_PRO_BOWL = ("NFL Pro Bowl wagers will have action regardless of number "
                   "of periods played and regardless of the minutes played in "
                   "each period.")
Q_BOOK_VENUE_CHANGED = ("Unless otherwise specified in a particular "
                        "sport’s rules, all bets on a fixture will be "
                        "voided if the venue is changed.")
Q_BOOK_PRECEDENCE = ("In case of any contradictions: Market Rules take "
                     "precedence over Sport Rules; which take precedence over "
                     "General Rules.")
Q_BOOK_LEAGUES = ("All American Football rules apply to NFL, NCAA, UFL, and "
                  "CFL unless a specific league is mentioned within the rule.")


def _book_cite(quote, where):
    return {"source": "Pinnacle betting rules -- %s" % where,
            "source_url": PINNACLE_RULES_URL,
            "retrieved_at": PINNACLE_CAPTURES[-1]["retrieved_at"],
            "page_sha256": PINNACLE_PAGE_SHA256, "quote": quote}


#: The venue's own listing text, as served by its public gateway to the
#: GitHub runner, for EVERY NFL contract read: the London game, the 1 pm
#: slate, the Sunday-night game and the Monday-night game carry the same
#: four rule sentences. Production agrees, on EVERY row: research-sql run
#: 37231923822 (job 111523310648, research/r30a_nfl_wording_and_catalogue_
#: shape.sql, read 2026-10-04T20:23:54Z) grouped all 15 NFL valuation rows
#: (15 contracts, every one carrying a rules text) by their wording with the
#: game name and date masked, with no limit: ONE wording, 15 of 15 rows.
#: (The first read, run 37226814972 M4, grouped the UNMASKED text with
#: LIMIT 10, so it showed only 10 of the 15 rows; the claim then outran it.)
VENUE_CAPTURES = (
    {"retrieved_at": "2026-10-04T04:29:04Z", "run_id": 37177121911,
     "job_id": 111361963055,
     "sha256": ("1a9d1c2fc79414dc61c3b676ad2d9d550cc5b6b209ac761e00f1f79269e1"
                "ebaa"),
     "slugs": ("aec-nfl-ind-was-2026-10-04", "aec-nfl-lar-phi-2026-10-04",
               "aec-nfl-nyj-chi-2026-10-04"),
     "held_in": "tests/fixtures/pmus_nfl_listing_2026_10_04.json"},
    {"retrieved_at": "2026-10-04T18:50:46Z", "run_id": 37225983304,
     "job_id": 111505618185,
     "sha256": ("02ff2cf8df8d1b786883622f761b70c8e7d1a93340eef54fc5c2389bdec7"
                "cb4d"),
     "slugs": ("aec-nfl-det-car-2026-10-04", "aec-nfl-atl-no-2026-10-05"),
     "held_in": "tests/fixtures/pmus_nfl_listing_2026_10_04_snf_mnf.json"},
)
VENUE_GATEWAY = "https://gateway.polymarket.us/v1/markets?slug=<slug>"
Q_VENUE_WINNER = ("This market will settle to the winner of the <away> vs "
                  "<home> NFL game scheduled for <Mon D, YYYY>.")
Q_VENUE_OVERTIME = "Overtime is included if played."
Q_VENUE_TIE = "If the game ends in a tie, the market will settle to $0.50."
Q_VENUE_POSTPONED = ("If the game is delayed, postponed, or suspended and not "
                     "rescheduled to a date within two weeks of the originally "
                     "scheduled date, the market will settle to the last fair "
                     "market price.")


def _venue_cite(quote):
    c = VENUE_CAPTURES[-1]
    return {"source": "Polymarket US listing description (public gateway)",
            "source_url": VENUE_GATEWAY, "retrieved_at": c["retrieved_at"],
            "sha256": c["sha256"], "quote": quote}


# ═════════════════════════════════════════════════════════════════════
# THE TIE FREQUENCY UNDER THE CURRENT OVERTIME RULE (cited, counted)
# ═════════════════════════════════════════════════════════════════════
#
# WHY THE CURRENT RULE ONLY. The 2025 change lets both teams possess in the
# 10-minute overtime even after a first-possession touchdown, which changes
# how often a game is still level when the period expires. Pooling the
# 2017-2024 seasons (a different rule) would borrow precision from a
# different process. The sample is therefore small, and the interval is wide
# -- which is the honest consequence, and the worst-case evaluation pays for
# it on every favourite.
#
# THE DENOMINATOR IS BRACKETED, NOT GUESSED. The count is stated "through
# Week 3 of the 2026 season". The 2025 regular season is 272 games (the
# league's own "272-game" schedule); 2026 Weeks 1-3 add at most 16 games a
# week (32 clubs). The exact 2026 count is not captured, so the interval is
# taken across the whole bracket: its lower end at the LARGEST possible
# denominator and its upper end at the SMALLEST, so it contains every
# interval the true denominator could give.
TIE_RATE_EVIDENCE = {
    "rule": ("NFL regular-season overtime since 2025: one 10-minute period, "
             "both teams may possess even if the first scores a touchdown; "
             "level after it, the game ends tied"),
    "rule_quote": ("In 2025, the NFL changed the overtime rules to allow both "
                   "teams to have possession during the overtime period, even "
                   "if the first team scores a touchdown, with the overtime "
                   "remaining at 10 minutes."),
    "definition_quote": ("In the National Football League (NFL), a tied game "
                         "occurs when a regular season game ends with both "
                         "teams having an equal score after one 10-minute "
                         "overtime period."),
    "count_quote": ("Through Week 3 of the 2026 season, a total of 17 regular "
                    "season games went to overtime, 1 ({{Percentage|1|17|1|"
                    "pad=yes}}) of which ended in a tie."),
    "corroboration_quote": ("The most recent tie game occurred on September "
                            "28, 2025, when the Green Bay Packers and Dallas "
                            "Cowboys played to a 40–40 draw."),
    "tie_games": ({"date": "2025-09-28", "away": "Green Bay Packers",
                   "home": "Dallas Cowboys", "score": "40-40"},),
    "ties": 1,
    "overtime_games": 17,
    "window": "2025 season start through Week 3 of the 2026 season",
    "source": ("Wikipedia, 'List of NFL tied games', revision 1377928469 "
               "(2026-10-02T00:27:40Z), section '2025–present' and the "
               "lead section"),
    "source_url": ("https://en.wikipedia.org/w/index.php?title="
                   "List_of_NFL_tied_games&oldid=1377928469"),
    "retrievals": (
        {"what": "section 8 '2025-present' wikitext, revision 1377928469",
         "retrieved_at": "2026-10-04T18:59:15Z", "run_id": 37226513169,
         "job_id": 111507171658, "bytes": 2135,
         "sha256": ("c273836bf3278034a6e3f240a86e2cd3df479b8ba2014f41d014fa"
                    "f1f5fbbba2")},
        {"what": "section 0 (lead) wikitext, revision 1377928469",
         "retrieved_at": "2026-10-04T18:55:23Z", "run_id": 37226271718,
         "job_id": 111506466636, "bytes": 5607,
         "sha256": ("a9bc4f2005815a9659addf8fb8e439e541f168b2c49222fa89075c"
                    "af04e0d47d")},
        {"what": "revision history (newest revision 1377928469)",
         "retrieved_at": "2026-10-04T18:55:31Z", "run_id": 37226283325,
         "job_id": 111506490371,
         "sha256": ("5a47704f2bbe96e772963efc1478b33bc8decebad8d79f73dbf36d"
                    "48c8e21651")}),
    "held_in": "tests/fixtures/nfl_tie_rate_evidence_2026_10_04.json",
    "games_min": 272,
    "games_min_basis": (
        "the complete 2025 regular season, on the league's own schedule size: "
        "\"It takes thousands of computers and a team of NFL executives to "
        "create the NFL’s 272-game masterpiece.\" (operations.nfl.com, "
        "read 2026-10-04T18:50:40Z, fetch-docs run 37225975369 job "
        "111505599478)"),
    "games_max": 272 + 3 * 16,
    "games_max_basis": ("plus 2026 Weeks 1-3 at most: 16 games a week among "
                        "the 32 clubs, 3 x 16 = 48"),
    "scope": ("REGULAR SEASON ONLY: the definition above and the count are "
              "regular-season games, so the interval is used only for a game "
              "ESTABLISHED as regular season (season_phase, from the cited "
              "season window) -- never for a preseason, postseason, Pro Bowl "
              "or any other game, about whose tie frequency it says nothing"),
}

CONFIDENCE = 0.95
INTERVAL_METHOD = ("Clopper-Pearson exact two-sided %.0f%%: lower end at the "
                   "largest possible denominator, upper end at the smallest"
                   % (CONFIDENCE * 100))


def _log_binom_pmf(i: int, n: int, p: float) -> float:
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p). Pure, log-space."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    return min(1.0, sum(math.exp(_log_binom_pmf(i, n, p))
                        for i in range(0, min(k, n) + 1)))


def _bisect(f, lo=0.0, hi=1.0, iters=200):
    """The root of a function increasing from f(lo) < 0 to f(hi) > 0."""
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, conf: float = CONFIDENCE) -> tuple:
    """The exact two-sided binomial interval for k successes in n. Pure."""
    k, n = int(k), int(n)
    if n <= 0 or k < 0 or k > n:
        raise ValueError("need 0 <= k <= n and n > 0 (k=%r, n=%r)" % (k, n))
    a = (1.0 - float(conf)) / 2.0
    # lower: P(X >= k | p) = a, increasing in p
    lo = 0.0 if k == 0 else _bisect(
        lambda p: (1.0 - binom_cdf(k - 1, n, p)) - a)
    # upper: P(X <= k | p) = a, decreasing in p
    hi = 1.0 if k == n else _bisect(lambda p: a - binom_cdf(k, n, p))
    return (lo, hi)


def tie_rate_interval(evidence: dict | None = None) -> dict:
    """The evidenced regular-season tie-rate interval, or why there is none."""
    ev = TIE_RATE_EVIDENCE if evidence is None else evidence
    try:
        k = int(ev["ties"])
        n_min, n_max = int(ev["games_min"]), int(ev["games_max"])
        if not (0 <= k <= n_min <= n_max):
            raise ValueError("inconsistent counts")
    except (KeyError, TypeError, ValueError) as exc:
        return {"held": False, "refusal": R_TIE_RATE_NOT_HELD,
                "why": "tie-rate evidence unusable: %s" % exc}
    lo = clopper_pearson(k, n_max)[0]
    hi = clopper_pearson(k, n_min)[1]
    return {"held": True, "lo": lo, "hi": hi, "ties": k,
            "games_min": n_min, "games_max": n_max,
            "point_estimate_range": (k / n_max, k / n_min),
            "confidence": CONFIDENCE, "method": INTERVAL_METHOD,
            "source": ev.get("source"), "source_url": ev.get("source_url")}


# ═════════════════════════════════════════════════════════════════════
# REFUSALS (named; each says what is missing)
# ═════════════════════════════════════════════════════════════════════

R_TIE_RATE_NOT_HELD = "NFL_TIE_RATE_EVIDENCE_NOT_HELD"
R_VENUE_TEXT_ABSENT = "NFL_VENUE_RULES_TEXT_NOT_RECORDED"
R_VENUE_TIE_NOT_STATED = "NFL_VENUE_TIE_PAYOUT_NOT_STATED"
R_VENUE_TIE_NOT_HALF = "NFL_VENUE_TIE_PAYOUT_IS_NOT_THE_CITED_HALF"
R_BOOK_PRICES_DRAW = "NFL_BOOK_LINE_PRICES_A_DRAW_NOT_THE_TWO_WAY_GAME_LINE"
R_EXHIBITION = "NFL_PRO_BOWL_OR_EXHIBITION_NEVER_TRADED"
R_DATE_INCONSISTENT = "NFL_FIXTURE_DATE_NOT_CONSISTENT_WITH_THE_VENUE_SLUG"
R_DATE_UNREADABLE = "NFL_FIXTURE_DATE_NOT_READABLE_FROM_THE_VENUE_SLUG"
R_PHASE_NOT_REGULAR = "NFL_SEASON_PHASE_NOT_ESTABLISHED_AS_REGULAR_SEASON"

REFUSALS = (R_TIE_RATE_NOT_HELD, R_VENUE_TEXT_ABSENT, R_VENUE_TIE_NOT_STATED,
            R_VENUE_TIE_NOT_HALF, R_BOOK_PRICES_DRAW, R_EXHIBITION,
            R_DATE_INCONSISTENT, R_DATE_UNREADABLE, R_PHASE_NOT_REGULAR)

#: WHAT THE STRICT POLICY (every condition paid identically on both sides)
#: STILL LACKS FOR AN NFL MONEY LINE. Derek's SETTLEMENT_NOT_SUPPORTED stays,
#: and this is its exact content: two payout differences no reading of the
#: prose can remove, and three conditions the venue's text does not address.
#: The payouts are keyed `book_payout` / `venue_payout` (as in
#: SETTLEMENT_STATES), never a bare "venue": that key names a TRADING VENUE
#: across the package, and tests/test_the_venue_identifier_reaches_the_
#: position_model.py rightly refuses a payout sentence spelled as one.
STRICT_POLICY_MISSING = (
    {"condition": "TIE_AFTER_OVERTIME",
     "book_payout": "VOID (stake returned)",
     "venue_payout": "0.50 per contract",
     "what_would_close_it": ("nothing in either document: the payouts "
                             "differ. Only a policy that PRICES the tie "
                             "(the completed-game policy, with the cited "
                             "tie-rate interval) can use the book's price")},
    {"condition": "POSTPONED_OR_NOT_RESCHEDULED",
     "book_payout": "VOID if not started within 12 h of the original time",
     "venue_payout": ("graded on a game rescheduled within two weeks; "
                      "otherwise the last fair market price S in [0, 1]"),
     "what_would_close_it": ("a MEASURED frequency of NFL postponement / "
                             "rescheduling beyond 12 h and of the price "
                             "branch, with E[S | branch]. Neither is held; "
                             "no feed here reports postponements")},
    {"condition": "SUSPENDED_CALLED_OR_STOPPED_EARLY",
     "book_payout": ("< 55 min and not completed in 12 h: VOID; >= 55 min "
                     "and not resumed in 12 h: the score at the suspension "
                     "is final"),
     "venue_payout": ("the listing states only the not-rescheduled-in-two-"
                      "weeks price branch; it states no payout for a game "
                      "suspended and made official, or resumed"),
     "what_would_close_it": ("the venue's per-condition rule for a suspended "
                             "NFL game (its rules text does not state one), "
                             "or a measured frequency as above")},
)


# ═════════════════════════════════════════════════════════════════════
# THE VENUE'S TIE PAYOUT, READ FROM ITS OWN TEXT
# ═════════════════════════════════════════════════════════════════════

#: THE CITED SENTENCE, EXACTLY (lower-cased, whitespace collapsed). R30A
#: review: the first reading searched for the sentence's OPENING words
#: anywhere, so "... settle to $0.50 for Yes and 1.00 for No." read as the
#: cited half, and a contradicting tie clause APPENDED to the cited text
#: ("If the game ends in a tie after overtime, all positions resolve to No.")
#: was never seen -- the contract ENTERED through the real paper pass. Now
#: every sentence of the text that speaks of a tie or a draw is read, and the
#: tie is priced only when there is exactly ONE such sentence and it IS the
#: cited one, terminated. Anything else -- a variant wording, an asymmetric
#: payout, a second tie clause -- is a named refusal: the outcome partition
#: is not the one the conversion prices.
TIE_SENTENCE = Q_VENUE_TIE.lower()
_TIE_WORD = re.compile(r"\b(?:ties?|tied|tying|draws?|drawn)\b")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def tie_sentences(prose) -> list:
    """Every sentence of the contract's text that speaks of a tie or a draw
    (lower-cased, whitespace collapsed), in order. Pure."""
    flat = " ".join(str(prose or "").split()).lower()
    return [x for x in _SENTENCE_END.split(flat) if _TIE_WORD.search(x)]


def venue_tie_payout(prose) -> dict:
    """{"payout": 0.5 | None, "refusal": ...} from the contract's own text:
    0.5 only when its ONE tie sentence is exactly the cited one."""
    flat = " ".join(str(prose or "").split()).lower()
    if not flat:
        return {"payout": None, "refusal": R_VENUE_TEXT_ABSENT,
                "why": "no venue rules text is recorded for this contract"}
    ties = tie_sentences(flat)
    if not ties:
        return {"payout": None, "refusal": R_VENUE_TIE_NOT_STATED,
                "why": ("the contract's text states no payout for a tied game, "
                        "so the outcome partition of an NFL game cannot be "
                        "priced on it")}
    last = _SENTENCE_END.split(flat)[-1]
    exact = [t for t in ties if t == TIE_SENTENCE or (
        t == last and t == TIE_SENTENCE[:-1])]
    if len(ties) == 1 and len(exact) == 1:
        return {"payout": 0.5, "refusal": None, "quote": Q_VENUE_TIE,
                "cite": _venue_cite(Q_VENUE_TIE)}
    return {"payout": None, "refusal": R_VENUE_TIE_NOT_HALF,
            "tie_sentences": ties,
            "why": ("the contract's tie text is not exactly the cited $0.50 "
                    "settlement (%d tie sentence(s), %d of them the cited "
                    "one): a variant, asymmetric or additional tie clause "
                    "establishes nothing" % (len(ties), len(exact)))}


def _draw_named(names) -> bool:
    return any(str(n).strip().lower() in ("draw", "tie", "x")
               for n in (names or ()))


# ═════════════════════════════════════════════════════════════════════
# THE CONVERSION: BOOK'S CONDITIONAL PRICE -> THE VENUE CONTRACT'S VALUE
# ═════════════════════════════════════════════════════════════════════

PHASE_REGULAR = "REGULAR_SEASON"


def interval_for(phase=None, evidence=None) -> dict:
    """The tie-rate interval FOR A GAME ESTABLISHED AS REGULAR SEASON, or a
    named refusal.

    R30A review: this used to return, for a game whose phase was not
    established, "the union of both cases" (the regular-season upper end
    with a lower end of 0) -- and every caller passed phase None. A
    preseason game or an exhibition is played under other rules (the review
    cites the 2021 removal of preseason overtime; not captured here) and
    lies outside what the evidence counts, yet an August game with no
    "preseason" word in its text was ENTERED and priced on the regular-
    season tie rate. The evidence counts
    regular-season games only (TIE_RATE_EVIDENCE["scope"]), so it now prices
    only a game positively ESTABLISHED as regular season (season_phase);
    every other phase -- preseason, postseason, Pro Bowl, unknown, or a
    caller that forgot to say -- fails closed by name."""
    iv = tie_rate_interval(evidence)
    if not iv.get("held"):
        return iv
    if str(phase or "") == PHASE_REGULAR:
        return dict(iv, phase=PHASE_REGULAR,
                    phase_basis=("the fixture is established as regular "
                                 "season (its America/New_York game day lies "
                                 "inside the cited season window)"))
    return {"held": False, "refusal": R_PHASE_NOT_REGULAR, "phase": phase,
            "why": ("the fixture is not established as a regular-season game "
                    "(phase %r); the cited tie-rate evidence counts regular-"
                    "season games only and says nothing about any other"
                    % (phase,))}


def venue_value(p_book, *, tie_payout: float = 0.5, phase=None,
                evidence=None) -> dict:
    """The held side's expected venue payout at the WORST end of the tie-rate
    interval. `p_book` is the book's de-vigged probability of the event this
    contract pays on, conditional on no tie. Pure."""
    iv = interval_for(phase, evidence)
    if not iv.get("held"):
        return {"p": None, "refusal": iv["refusal"], "why": iv.get("why")}
    p = float(p_book)
    lo, hi = float(iv["lo"]), float(iv["hi"])

    def v(t):
        return (1.0 - t) * p + float(tie_payout) * t
    v_lo, v_hi = v(lo), v(hi)
    if v_hi <= v_lo:
        t_used, worst, end = hi, v_hi, "HIGHEST"
    else:
        t_used, worst, end = lo, v_lo, "LOWEST"
    return {
        "p": round(worst, 12), "refusal": None,
        "p_book_conditional_no_tie": p,
        "tie_payout_per_contract": float(tie_payout),
        "tie_rate_used": t_used, "tie_rate_end_used": end,
        "tie_rate_interval": [lo, hi],
        "p_venue_at_interval_ends": [round(v_lo, 12), round(v_hi, 12)],
        "formula": "p_venue = (1 - t) * p_book + %.2f * t" % float(tie_payout),
        "worst_case_rule": (
            "the end of the evidenced interval that LOWERS the held side's "
            "value: the highest tie rate when p_book > %.2f (a tie pays less "
            "than the side's conditional win chance), the lowest otherwise"
            % float(tie_payout)),
        "interval": iv}


def convert(p_book, *, sport_family, venue_rules_text,
            book_outcome_names=None, phase=None, league=None,
            evidence=None) -> dict:
    """THE CONVERSION THE COMPLETED-GAME POLICY APPLIES BEFORE ANY EDGE.

    Returns {"applies": bool, "p": float | None, "refusal": str | None, ...}.
    Not football, or football outside the NFL: applies False and `p` is
    returned unchanged (nothing here alters another sport). NFL: the venue's
    stated tie payout must be the cited $0.50, the book's line must be the
    two-way game line (no Draw priced), and the tie-rate evidence must be
    held; any miss is a named refusal with `p` None.
    """
    fam = str(sport_family or "").strip().lower()
    lg = None if league is None else str(league).strip().lower()
    if fam != "football" or (lg is not None and lg != "nfl"):
        return {"applies": False, "p": p_book, "refusal": None,
                "why": "no tie conversion outside the NFL money line"}
    out = {"applies": True, "version": VERSION, "p": None, "refusal": None,
           "p_book_conditional_no_tie": p_book,
           "book_tie_rule": _book_cite(Q_BOOK_TIE, "General Rules"),
           "book_overtime_rule": _book_cite(Q_BOOK_OVERTIME,
                                            "American Football")}
    if p_book is None:
        out["why"] = "no book probability to convert (refused upstream)"
        return out
    if _draw_named(book_outcome_names):
        out.update(refusal=R_BOOK_PRICES_DRAW, why=(
            "the book line prices a Draw. A draw-priced American-football "
            "money line is a regulation (three-way) market, a different "
            "event from the venue's overtime-included contract; deriving the "
            "full-game chance from it needs an overtime model nobody holds"))
        return out
    vt = venue_tie_payout(venue_rules_text)
    out["venue_tie_rule"] = vt
    if vt.get("refusal"):
        out.update(refusal=vt["refusal"], why=vt.get("why"))
        return out
    vv = venue_value(p_book, tie_payout=vt["payout"], phase=phase,
                     evidence=evidence)
    if vv.get("refusal"):
        out.update(refusal=vv["refusal"], why=vv.get("why"))
        return out
    out.update(vv, applies=True, refusal=None)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE STATE TABLE (payouts, citations, class)
# ═════════════════════════════════════════════════════════════════════

ORDINARY = "ORDINARY_COMPLETION_IN_THE_CONDITIONAL_EV"
EXCEPTIONAL = "EXCEPTIONAL_DISCLOSED_PROBABILITY_UNMEASURED"
EXCLUDED = "EXCLUDED_NEVER_TRADED"
#: A PAID STATE THAT IS NOT KNOWN (R30A review): a 0.50 settlement on a
#: contract whose text states both the tie settlement and the
#: last-fair-market-price clause, with no final score read. Neither the
#: ordinary nor the exceptional class is claimed for it.
AMBIGUOUS = "ORDINARY_TIE_OR_EXCEPTIONAL_PRICE_NOT_DISTINGUISHED"
S_TIE_OR_LAST_FAIR_PRICE = "TIE_OR_LAST_FAIR_PRICE_NOT_DISTINGUISHED"

SETTLEMENT_STATES = (
    {"state": "REGULATION_WIN", "class": ORDINARY,
     "venue_payout": "1 on the winner's side, 0 on the other",
     "book_payout": "the bet on the winner wins at its price, the other loses",
     "verdict": "SAME_EVENT_SAME_DIRECTION",
     "venue_cite": _venue_cite(Q_VENUE_WINNER),
     "book_cite": _book_cite(Q_BOOK_OVERTIME, "American Football")},
    {"state": "OVERTIME_WIN", "class": ORDINARY,
     "venue_payout": "1 on the winner's side, 0 on the other",
     "book_payout": "the bet on the winner wins at its price, the other loses",
     "verdict": "SAME_EVENT_SAME_DIRECTION",
     "venue_cite": _venue_cite(Q_VENUE_OVERTIME),
     "book_cite": _book_cite(Q_BOOK_OVERTIME, "American Football")},
    {"state": "TIE_AFTER_OVERTIME", "class": ORDINARY,
     "venue_payout": 0.5, "book_payout": "VOID (stake returned)",
     "verdict": "DIFFERENT_PAYOUT_PRICED_BY_THE_TIE_CONVERSION",
     "venue_cite": _venue_cite(Q_VENUE_TIE),
     "book_cite": _book_cite(Q_BOOK_TIE, "General Rules"),
     "consequence": ("the book's de-vigged two-way price is conditional on no "
                     "tie; the contract is valued at (1 - t) p + 0.5 t at the "
                     "worst end of the cited tie-rate interval")},
    {"state": "POSTPONED_RESCHEDULED_WITHIN_TWO_WEEKS", "class": EXCEPTIONAL,
     "venue_payout": ("graded on the rescheduled game (the price clause fires "
                      "only when NOT rescheduled within two weeks; the venue "
                      "does not state this payout in its own words)"),
     "book_payout": "VOID when not started within 12 h of the original time",
     "verdict": "DIFFERENT_PAYOUT",
     "venue_cite": _venue_cite(Q_VENUE_POSTPONED),
     "book_cite": _book_cite(Q_BOOK_NOT_STARTED, "American Football")},
    {"state": "POSTPONED_NOT_RESCHEDULED_WITHIN_TWO_WEEKS",
     "class": EXCEPTIONAL,
     "venue_payout": "the last fair market price S, S in [0, 1]",
     "book_payout": "VOID (not started within 12 h)",
     "verdict": "DIFFERENT_PAYOUT",
     "venue_cite": _venue_cite(Q_VENUE_POSTPONED),
     "book_cite": _book_cite(Q_BOOK_NOT_STARTED, "American Football")},
    {"state": "SUSPENDED_OR_ABANDONED", "class": EXCEPTIONAL,
     "venue_payout": ("the last fair market price S when not rescheduled "
                      "within two weeks; otherwise not stated"),
     "book_payout": ("< 55 min and not completed in 12 h: VOID; >= 55 min "
                     "and not resumed in 12 h: the score at the suspension"),
     "verdict": "DIFFERENT_PAYOUT",
     "venue_cite": _venue_cite(Q_VENUE_POSTPONED),
     "book_cite": _book_cite(Q_BOOK_SUSPENDED, "American Football")},
    {"state": "VENUE_CHANGED", "class": EXCEPTIONAL,
     "venue_payout": "not stated",
     "book_payout": "VOID (General Rules, unless the sport rules say otherwise)",
     "verdict": "VENUE_SILENT",
     "book_cite": _book_cite(Q_BOOK_VENUE_CHANGED, "General Rules")},
    {"state": "PRO_BOWL_OR_EXHIBITION", "class": EXCLUDED,
     "venue_payout": "not a regular NFL fixture",
     "book_payout": "its own action rule (periods and minutes disregarded)",
     "verdict": "NEVER_TRADED",
     "book_cite": _book_cite(Q_BOOK_PRO_BOWL, "American Football")},
)


def states_record() -> dict:
    """The whole comparison for a decision record. Pure."""
    iv = tie_rate_interval()
    return {"version": VERSION, "states": [dict(s) for s in SETTLEMENT_STATES],
            "tie_rate_interval": ([iv.get("lo"), iv.get("hi")]
                                  if iv.get("held") else None),
            "exceptional_probability": "UNMEASURED",
            "exceptional_note": (
                "postponement, suspension and venue change pay differently on "
                "the two sides; their NFL frequency is not measured here, is "
                "never set to zero and is not part of the completed-game EV"),
            "strict_policy_missing": [dict(m) for m in STRICT_POLICY_MISSING]}


# ═════════════════════════════════════════════════════════════════════
# PRO BOWL / EXHIBITION: NEVER TRADED
# ═════════════════════════════════════════════════════════════════════

_EXHIBITION = (r"\bpro\s*bowl\b", r"\bpre-?season\b", r"\bexhibition\b",
               r"\bhall of fame game\b", r"\ball[- ]star\b",
               r"\bafc\b[^.]{0,20}\bvs\.?\s+nfc\b",
               r"\bnfc\b[^.]{0,20}\bvs\.?\s+afc\b",
               r"-(?:afc|nfc)-(?:afc|nfc)-")


def exhibition_marker(*texts) -> str | None:
    """The first Pro Bowl / exhibition marker in any of the texts, or None."""
    for t in texts:
        flat = " ".join(str(t or "").split()).lower()
        for pat in _EXHIBITION:
            m = re.search(pat, flat)
            if m:
                return m.group(0)
    return None


# ═════════════════════════════════════════════════════════════════════
# THE FIXTURE DATE: VENUE SLUG == VENUE PROSE == KICKOFF ON THE ET DAY
# ═════════════════════════════════════════════════════════════════════
#
# The venue dates an NFL contract by the America/New_York day of the game:
# the Sunday-night game kicking off at 00:20Z on Monday is
# aec-nfl-det-car-2026-10-04 and its text says "scheduled for Oct 4, 2026";
# the London game at 13:30Z (09:30 ET) is aec-nfl-ind-was-2026-10-04. A
# mapping that dated by the UTC day would put the Sunday-night game on the
# wrong day. All three readings must agree, or the decision refuses by name.

ET = "America/New_York"
_SLUG_DATE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})$")
_PROSE_DATE = re.compile(r"\bscheduled for ([a-z]{3,9})\.? (\d{1,2}), (\d{4})")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"))}


def _prose_date(prose):
    import datetime as _dt
    m = _PROSE_DATE.search(" ".join(str(prose or "").split()).lower())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3])
    if mon is None:
        return None
    try:
        return _dt.date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def fixture_date(*, slug, venue_rules_text=None, kickoff_epoch=None) -> dict:
    """The NFL contract's event date and whether its three readings agree."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    out = {"event_date": None, "refusal": None, "basis": None,
           "slug": slug, "prose_date": None, "kickoff_utc": None,
           "kickoff_et": None, "kickoff_utc_date": None,
           "kickoff_et_date": None}
    m = _SLUG_DATE.search(str(slug or ""))
    if not m:
        out.update(refusal=R_DATE_UNREADABLE,
                   why="the venue slug carries no YYYY-MM-DD date")
        return out
    try:
        sd = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        out.update(refusal=R_DATE_UNREADABLE, why="the slug date is invalid")
        return out
    pd = _prose_date(venue_rules_text)
    out["prose_date"] = None if pd is None else pd.isoformat()
    problems = []
    if pd is not None and pd != sd:
        problems.append("slug %s vs venue text %s" % (sd, pd))
    if kickoff_epoch is not None:
        try:
            k = float(kickoff_epoch)
            ku = _dt.datetime.fromtimestamp(k, _dt.timezone.utc)
            ke = _dt.datetime.fromtimestamp(k, ZoneInfo(ET))
            out.update(kickoff_utc=ku.isoformat(), kickoff_et=ke.isoformat(),
                       kickoff_utc_date=ku.date().isoformat(),
                       kickoff_et_date=ke.date().isoformat())
            if ke.date() != sd:
                problems.append("slug %s vs kickoff on the ET day %s"
                                % (sd, ke.date()))
        except (TypeError, ValueError, OverflowError):
            out["kickoff_note"] = "kickoff instant unreadable; not used"
    else:
        out["kickoff_note"] = "kickoff instant not held; not used"
    if problems:
        out.update(refusal=R_DATE_INCONSISTENT, why="; ".join(problems))
        return out
    out.update(event_date=sd.isoformat(),
               basis=("the venue slug's date, which is the game's "
                      "America/New_York day%s%s"
                      % (" (agrees with the venue text)" if pd else "",
                         " (agrees with the kickoff instant on the ET day)"
                         if out["kickoff_et_date"] else "")))
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SEASON PHASE: ESTABLISHED FROM A CITED WINDOW, NEVER ASSUMED
# ═════════════════════════════════════════════════════════════════════
#
# WHY A WINDOW AND NOT A KEYWORD (R30A review). The first pass excluded the
# Pro Bowl / exhibitions by searching the slug and text for marker words and
# priced every other game as regular season, so a preseason listing worded
# like the regular-season ones (no venue preseason listing has ever been
# captured, so its wording is unknown) would have been ENTERED. The phase is
# now ESTABLISHED POSITIVELY: a game is regular season only when its
# America/New_York game day (the venue slug's date, which fixture_date checks
# against the venue text and the kickoff) lies inside a cited regular-season
# window. Every NFL game played on a day inside that window is a regular-
# season game; a day before it (preseason, the Hall of Fame Game) or after
# it (the playoffs, the Pro Bowl, the Super Bowl) is not established and is
# refused by name. The marker words stay as an EXTRA refusal only.
#
# THE POSTSEASON IS REFUSED, NOT PRICED. The window after the regular season
# holds the playoffs AND exhibitions this source does not date, the venue
# and catalogue carry no phase field, and no postseason overtime rule text
# was captured; a playoff game therefore cannot be established as one here.
# What would admit it: a venue or catalogue phase field, or the playoff
# fixtures themselves, plus the captured postseason overtime rule.

SEASON_WINDOWS = (
    {"season": 2026,
     "regular_season_first_day": "2026-09-09",
     "regular_season_last_day": "2027-01-10",
     "playoffs_first_day": "2027-01-16",
     "super_bowl_day": "2027-02-14",
     "infobox_quote": ("| regular_season = {{Start date|2026|09|09|}} – "
                       "{{End date|2027|01|10}}"),
     "lead_quote": ("The regular season began on September 9, 2026, with "
                    "reigning Super Bowl champion Seattle defeating New "
                    "England in the NFL Kickoff Game, and will end on January "
                    "10, 2027. The playoffs will begin on January 16 and "
                    "conclude with Super Bowl LXI at SoFi Stadium in "
                    "Inglewood, California, on February 14."),
     "source": "Wikipedia, '2026 NFL season', revision 1378055076, lead "
               "section (infobox and first paragraph)",
     "source_url": ("https://en.wikipedia.org/w/index.php?title=2026_NFL_"
                    "season&oldid=1378055076"),
     "retrieved_at": "2026-10-04T20:20:17Z", "run_id": 37231698293,
     "job_id": 111522586473, "bytes": 1254,
     "sha256": ("5cc6b1025c1fae1e7343706556d3368025aad4ec0dc8276e8983546d99"
                "d54be6"),
     "held_in": "tests/fixtures/nfl_season_window_2026_10_04.json"},
)


def season_phase(event_date) -> dict:
    """{"phase": "REGULAR_SEASON" | None, "refusal": ..., ...} for an NFL
    game's America/New_York day (a date or 'YYYY-MM-DD'). Pure."""
    import datetime as _dt
    try:
        d = (event_date if isinstance(event_date, _dt.date)
             else _dt.date.fromisoformat(str(event_date)))
    except (TypeError, ValueError):
        return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
                "event_date": event_date,
                "why": "the game day is not established, so neither is its "
                       "phase"}
    for w in SEASON_WINDOWS:
        first = _dt.date.fromisoformat(w["regular_season_first_day"])
        last = _dt.date.fromisoformat(w["regular_season_last_day"])
        cite = {k: w[k] for k in ("source", "source_url", "retrieved_at",
                                  "run_id", "job_id", "sha256",
                                  "infobox_quote")}
        if first <= d <= last:
            return {"phase": PHASE_REGULAR, "refusal": None,
                    "event_date": d.isoformat(), "season": w["season"],
                    "window": [w["regular_season_first_day"],
                               w["regular_season_last_day"]],
                    "basis": ("the game day %s lies inside the cited %d "
                              "regular season (%s .. %s)"
                              % (d, w["season"], first, last)),
                    "cite": cite}
        if d < first and d.year == first.year:
            return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
                    "event_date": d.isoformat(), "season": w["season"],
                    "cite": cite,
                    "why": ("the game day %s is BEFORE the cited %d regular "
                            "season (first day %s): a preseason or exhibition "
                            "game, never traded" % (d, w["season"], first))}
        if last < d <= last.replace(month=3, day=31):
            return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
                    "event_date": d.isoformat(), "season": w["season"],
                    "cite": cite,
                    "why": ("the game day %s is AFTER the cited %d regular "
                            "season (last day %s): playoffs or an exhibition "
                            "(Pro Bowl), which this window cannot tell apart; "
                            "not established, never traded"
                            % (d, w["season"], last))}
    return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
            "event_date": d.isoformat(),
            "why": ("no cited season window covers the game day %s; its phase "
                    "is not established" % d)}


def phase_of_slug(slug) -> dict:
    """season_phase of the venue slug's own date (the game's America/
    New_York day). For readers that hold only the contract (Xavier's measure,
    the maker's re-check); the entry decision also checks that date against
    the venue text and the kickoff (fixture_date). Pure."""
    import datetime as _dt
    m = _SLUG_DATE.search(str(slug or ""))
    if not m:
        return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
                "why": "the venue slug carries no YYYY-MM-DD date"}
    try:
        d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return {"phase": None, "refusal": R_PHASE_NOT_REGULAR,
                "why": "the venue slug's date is invalid"}
    return season_phase(d)


def league_of_slug(slug) -> str | None:
    """'nfl' for aec-nfl-..., the venue's league token, else None. Pure."""
    parts = str(slug or "").split("-")
    return parts[1] if len(parts) > 2 and parts[0] == "aec" else None


def describe() -> dict:
    iv = tie_rate_interval()
    return {"version": VERSION,
            "tie_rate_interval": ([iv.get("lo"), iv.get("hi")]
                                  if iv.get("held") else None),
            "tie_rate_method": INTERVAL_METHOD,
            "tie_rate_source": TIE_RATE_EVIDENCE["source_url"],
            "regular_season_windows": [
                [w["regular_season_first_day"], w["regular_season_last_day"]]
                for w in SEASON_WINDOWS],
            "refusals": list(REFUSALS),
            "states": [s["state"] for s in SETTLEMENT_STATES],
            "strict_policy_missing": [m["condition"]
                                      for m in STRICT_POLICY_MISSING]}
