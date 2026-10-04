"""NCAAF MONEY LINE: THE VENUE CONTRACT AND PINNACLE'S BET, COMPARED AS PAYOUTS.

WHY THIS EXISTS (P0 incident, coverage -> trade starvation). Every college
football money line was refused: the completed-game policy found no
completed-game terms for the college board (NO_COMPLETED_GAME_TERMS_FOR_THIS_
SPORT), football outside the NFL stayed outside the de-vig set, and the strict
policy refused settlement (SETTLEMENT_NOT_SUPPORTED) without saying which
clause. The R30A NFL stream solved the same problem for the NFL
(bettor_nfl_settlement) and deliberately left the college board where it was,
because the college contract is a DIFFERENT contract: its tie clause is not
the NFL's. This module is the same integration for the college board, built
the same way: every settlement state of an NCAA game, what EACH side pays in
it (a number or a named payout class, never shared wording), the citation each
payout was read from, and -- the owner's rule -- a price is compared only
where the two payoff functions are genuinely equivalent.

PURE. Standard library only, plus the NFL module's pure date reader. Nothing
here imports a paper, live, funded or execution module, and nothing here
decides an order: callers (the completed-game policy, the strict policy's
refusal, Xavier's measure, the maker's re-check) read it.

──────────────────────────────────────────────────────────────────────────
THE PAYOUTS, STATE BY STATE (per $1 contract on the side held)

  state                      venue (Polymarket US cfb listing)  Pinnacle 2-way ML
  regulation win             1 / 0 on the winner                wins / loses
  overtime win               1 / 0 ("Overtime is included       wins / loses ("Game
                             if played.")                       ... include points
                                                                scored in overtime.")
  tie after overtime         UNREACHABLE in a completed NCAA game: overtime is
    (completed game)         played until a winner (NO_TIE_RULE, cited). The
                             book would void a draw; the venue would review it.
                             Neither branch can fire in an ordinarily completed
                             game, so P(tie | completed) = 0 by rule, not by
                             estimate.
  tied result declared       NOT STATED ("will not resolve      a game stopped and
    without a winner         automatically and will be          made final level is
    (a game NOT completed    reviewed against the official      a suspension: void
    under the rules)         governing-body result.")           (< 55 min) or the
                                                                score at suspension
                                                                (>= 55 min) -- a
                                                                draw, so void
  postponed / suspended      graded on a game rescheduled       VOID if not started
                             inside two weeks, otherwise the    in 12 h; suspension
                             last fair market price S in [0,1]  rules as above
  forfeit                    "Outcome sourced from the          VOID "regardless of
                             relevant governing body." (no      how the governing
                             separate forfeit payout stated)    body ... scores it"
  venue changed              not stated                         VOID (General Rules)

THE TIE IS NOT PRICED HERE BECAUSE IT CANNOT HAPPEN IN A COMPLETED GAME -- AND
THAT IS A CITED RULE, NOT AN ASSUMPTION. The NFL contract pays 0.50 on a tie
that an ordinarily completed NFL game can reach, so the NFL module converts
the book's P(win | no tie) at the worst end of a cited tie-rate interval. An
NCAA game tied after regulation goes to overtime and keeps going until one
team wins (the cited rule), so a completed NCAA game is never tied: the
book's two-way price P(win | no tie) IS P(win | completed), and the venue
contract pays 1 on that win and 0 on that loss. The conversion is therefore
the identity -- but only while EVERY premise holds, each checked by name:
the venue's text is exactly the cited clauses (one wording, measured on every
production row), the book's line is the two-way game line (no Draw priced),
and the no-tie rule evidence is held. Any miss is a named refusal.

THE EXCEPTIONAL STATES ARE DISCLOSED, NOT PRICED. Postponement, suspension,
a tied result declared without a winner, a forfeit and a venue change pay
differently on the two sides (above) or are not stated by the venue; their
frequency is NOT measured here. They are carried as disclosed exceptional-
settlement risk, payoffs stated, probabilities UNMEASURED -- never set to
zero and never folded into the completed-game EV. For the STRICT policy
(every condition paid identically on both sides) they remain a refusal, and
STRICT_POLICY_MISSING names each one as its own code.
"""
from __future__ import annotations

import re

from . import bettor_nfl_settlement as _NFL

VERSION = "BETTOR_NCAAF_SETTLEMENT_V1"
LEAGUE = "cfb"
PROVIDER_KEY = "americanfootball_ncaaf"

# ═════════════════════════════════════════════════════════════════════
# THE BOOK'S CITATIONS (retrieved, not recalled)
# ═════════════════════════════════════════════════════════════════════
#
# THE SAME PAGE THE NFL MODULE CITES, AND THE SAME BYTES. Pinnacle's American
# Football sport rules state one rule set "for NFL, NCAA, UFL, and CFL unless a
# specific league is mentioned within the rule"; the only league-specific
# sport rule is the NFL Pro Bowl's. Its American Football MARKET Rules carry
# exactly two NCAA rules -- "NCAA Football Season Wins" and "NCAA Football
# Conference Championship Futures" -- neither a game money line, so by the
# publisher's own precedence (Market > Sport > General) the money line of an
# NCAA game is governed by the sport rules and then the General Rules. This
# stream re-read the page (third read, byte-identical: same size, same
# sha256) and read that market-rule list in full.

PINNACLE_RULES_URL = _NFL.PINNACLE_RULES_URL
PINNACLE_PAGE_SHA256 = _NFL.PINNACLE_PAGE_SHA256
PINNACLE_CAPTURES = tuple(_NFL.PINNACLE_CAPTURES) + (
    {"retrieved_at": "2026-10-04T22:51:54Z", "run_id": 37241517150,
     "job_id": 111551013667, "bytes": 119592,
     "sha256": PINNACLE_PAGE_SHA256,
     "held_in": ("this module (NCAAF stream re-read; unchanged; American "
                 "Football market rules read in full: the NCAA rules there "
                 "are Season Wins and Conference Championship Futures only)")},
)

Q_BOOK_LEAGUES = _NFL.Q_BOOK_LEAGUES
Q_BOOK_OVERTIME = _NFL.Q_BOOK_OVERTIME
Q_BOOK_NOT_STARTED = _NFL.Q_BOOK_NOT_STARTED
Q_BOOK_SUSPENDED = _NFL.Q_BOOK_SUSPENDED
Q_BOOK_TIE = _NFL.Q_BOOK_TIE
Q_BOOK_VENUE_CHANGED = _NFL.Q_BOOK_VENUE_CHANGED
Q_BOOK_PRECEDENCE = _NFL.Q_BOOK_PRECEDENCE
Q_BOOK_RESULT = ("The result of a fixture will be the final determination by "
                 "the fixture’s governing body on the date of the "
                 "fixture’s completion. Pinnacle does not recognise "
                 "protested or overturned decisions.")
Q_BOOK_FORFEIT = ("In any fixture involving a forfeit, walkover, or any other "
                  "event where the fixture is considered complete without "
                  "having been played, all bets will be voided, regardless "
                  "of how the governing body of its league scores it.")
Q_BOOK_NCAA_MARKET_RULES = (
    "NCAA Football Season Wins: Bowl games and Conference Championship games "
    "are not counted towards Regular Season Wins totals.",
    "NCAA Football Conference Championship Futures : If the conference has "
    "an official championship game, the winner of that game is considered "
    "to have won that conference.")
#: Every book sentence this module rests on (each is verbatim in the captured
#: page; tests/test_ncaaf_settlement_evidence.py checks every one against the
#: held capture).
BOOK_QUOTES = (Q_BOOK_LEAGUES, Q_BOOK_OVERTIME, Q_BOOK_NOT_STARTED,
               Q_BOOK_SUSPENDED, Q_BOOK_TIE, Q_BOOK_VENUE_CHANGED,
               Q_BOOK_PRECEDENCE, Q_BOOK_RESULT, Q_BOOK_FORFEIT)


def _book_cite(quote, where):
    c = PINNACLE_CAPTURES[-1]
    return {"source": "Pinnacle betting rules -- %s" % where,
            "source_url": PINNACLE_RULES_URL,
            "retrieved_at": c["retrieved_at"], "run_id": c["run_id"],
            "page_sha256": PINNACLE_PAGE_SHA256, "quote": quote}


# ═════════════════════════════════════════════════════════════════════
# THE VENUE'S CITATIONS: its public listing AND every production row
# ═════════════════════════════════════════════════════════════════════
#
# The venue's own listing text for three cfb money lines, served by its
# public gateway to the GitHub runner (cand22's capture), AND the text the
# collector persisted on EVERY production cfb valuation row: research-sql run
# 37241503567 (job 111550976439, research/incident_ncaaf_settlement_wording.
# sql, read 2026-10-04T22:54:12Z) grouped all 9 cfb valuation rows (9
# contracts, 9 of 9 carrying a rules text) by their wording with the game name
# and date masked, with no limit: ONE wording, 9 of 9 rows, and it is the
# gateway capture's wording sentence for sentence (N2); all 9 name a "College
# Football game scheduled for <date>." and none an NFL game (N2b).
VENUE_CAPTURES = (
    {"what": "public gateway listing, three cfb money lines",
     "retrieved_at": "2026-10-03T23:14:23Z", "run_id": 37161115118,
     "job_id": 111314569211,
     "sha256": ("1df8dc714088b1a2045e13ccb07aa6f4982314b25826e769c56c53ecc10"
                "106cc"),
     "slugs": ("aec-cfb-frest-washst-2026-10-03",
               "aec-cfb-bayl-arzst-2026-10-03", "aec-cfb-cin-arz-2026-10-03"),
     "held_in": "tests/fixtures/pmus_cfb_listing_2026_10_03.json"},
    {"what": ("production external_valuations.settlement_comparison."
              "venue_rules_text, every cfb row, masked wording groups"),
     "retrieved_at": "2026-10-04T22:54:12Z", "run_id": 37241503567,
     "job_id": 111550976439,
     "sql": "research/incident_ncaaf_settlement_wording.sql",
     "sql_sha256": ("0335ccc403d47fc789969fc97b5940d78bba3aac303519b7b011d6b2"
                    "59a1c853"),
     "rows": 9, "contracts": 9, "wordings": 1,
     "masked_md5": "f0828e4cff231ea08f7ef77099788595",
     "row_ids": (4970, 4971, 4977, 4989, 4990, 5017, 5020, 5066, 5131),
     "held_in": "tests/fixtures/ncaaf_production_wording_2026_10_04.json"},
)
VENUE_GATEWAY = "https://gateway.polymarket.us/v1/markets?slug=<slug>"
Q_VENUE_WINNER = ("This market will settle to the winner of the <away> vs "
                  "<home> College Football game scheduled for <Mon D, YYYY>.")
Q_VENUE_OVERTIME = "Overtime is included if played."
Q_VENUE_TIE_REVIEW = ("If a tied final score is reported and no official "
                      "winner is declared, this market will not resolve "
                      "automatically and will be reviewed against the "
                      "official governing-body result.")
Q_VENUE_POSTPONED = ("If the game is delayed, postponed, or suspended and not "
                     "rescheduled to a date within two weeks of the originally "
                     "scheduled date, the market will settle to the last fair "
                     "market price.")
Q_VENUE_SOURCE = "Outcome sourced from the relevant governing body."


def _venue_cite(quote):
    c = VENUE_CAPTURES[0]
    p = VENUE_CAPTURES[-1]
    return {"source": "Polymarket US cfb listing description (public gateway)",
            "source_url": VENUE_GATEWAY, "retrieved_at": c["retrieved_at"],
            "sha256": c["sha256"],
            "production_rows": {"run_id": p["run_id"],
                                "retrieved_at": p["retrieved_at"],
                                "rows": p["rows"], "wordings": p["wordings"]},
            "quote": quote}


# ═════════════════════════════════════════════════════════════════════
# THE NO-TIE RULE (cited): a completed NCAA game is never tied
# ═════════════════════════════════════════════════════════════════════

#
# WHAT IS CLAIMED. Only this: a college game level at the end of regulation
# goes to overtime, and overtime periods are repeated until one team wins, so
# an ordinarily COMPLETED college game is never tied. Nothing here claims a
# game cannot be STOPPED level (weather, a terminated game): that is not a
# completed game, and it is the exceptional state TIED_RESULT_DECLARED_
# WITHOUT_AN_OFFICIAL_WINNER below, which the venue's own review clause
# exists for.
#
# THE SOURCE IS SECONDARY, AND SAYS SO. The NCAA's own rules book was sought
# first: its publisher's old PDF path answers with a 404 page (fetch-docs run
# 37241681952), and its catalogue offers the 2025 book only as a checkout
# download (run 37242123927), which a read-only runner does not perform. The rule is therefore cited from an encyclopedia summary of
# one fixed revision, read by the GitHub runner, every sentence verbatim in
# tests/fixtures/ncaaf_no_tie_rule_2026_10_04.json. The same section names
# the leagues whose overtime CAN end level (the CFL before 2026, the AAF's
# regular season) and names no such limit for college football. If this
# evidence is withdrawn, every NCAAF conversion refuses by name
# (NCAAF_NO_TIE_RULE_EVIDENCE_NOT_HELD); nothing falls back to an assumption.
NO_TIE_RULE_EVIDENCE = {
    "rule": ("college football since 1996: a game tied after regulation is "
             "decided by overtime periods, repeated until a winner is "
             "determined; overtime points count as regulation points"),
    "rule_quote": ("In American college and high school football, the "
                   "overtime periods are continued until a winner is "
                   "determined."),
    "repeat_quote": ("If the score remains tied at the end of the first "
                     "overtime period, the procedure is repeated."),
    "scope_quote": ("In [[college football|college]] (since the [[1996 NCAA "
                    "Division I-A football season|1996 season]]) and [[high "
                    "school football]], as well as the [[Canadian Football "
                    "League]] (since the 2000 season) and the short-lived "
                    "[[Alliance of American Football]], an ''overtime "
                    "procedure'' is used to determine the winner."),
    "ncaa_quote": ("In [[College football|NCAA football]], since 2021, teams "
                   "must attempt a [[two-point conversion]] after a touchdown "
                   "in double overtime; all overtime procedures thereafter "
                   "consist of two-point conversion attempts and are scored "
                   "as such."),
    "points_quote": ("All points scored in overtime count as if they were "
                     "scored in regulation."),
    "source": ("Wikipedia, 'Overtime (sports)', revision 1377048726, section "
               "10 'College, high school, and Canadian football'"),
    "source_class": "SECONDARY",
    "source_url": ("https://en.wikipedia.org/w/index.php?title=Overtime_"
                   "(sports)&oldid=1377048726"),
    "retrieved_at": "2026-10-04T22:59:10Z",
    "run_id": 37241934626, "job_id": 111552230343, "bytes": 11968,
    "sha256": ("6e0c280c7bb6f71dd138235322307a61c6bf03e24a1838e6c27afc34230d"
               "c02d"),
    "revision_read": {"run_id": 37241519094, "job_id": 111551331365,
                      "retrieved_at": "2026-10-04T22:53:31Z",
                      "sha256": ("cd4fe87eed027494226e1c39952b55a76087f5423a"
                                 "7296605086455e095e8a18")},
    "primary_source_attempts": (
        {"url": "https://www.ncaapublications.com/productdownloads/FR25.pdf",
         "result": "404 page, not the rules book", "run_id": 37241681952,
         "job_id": 111551624059, "retrieved_at": "2026-10-04T22:54:54Z"},
        {"url": "https://www.ncaapublications.com/collections/rules-books",
         "result": ("the 2025 NCAA Football Rules Book is a $0.00 checkout "
                    "download, not a public URL"), "run_id": 37242123927,
         "job_id": 111552778129, "retrieved_at": "2026-10-04T23:02:33Z"}),
    "scope": ("every game played under college rules (regular season, "
              "conference championship, bowl, playoff): the summary states "
              "the rule for college football without a phase limit -- unlike "
              "the NFL evidence, no season window is needed"),
    "held_in": "tests/fixtures/ncaaf_no_tie_rule_2026_10_04.json",
}


# ═════════════════════════════════════════════════════════════════════
# REFUSALS (named; each says which clause is missing)
# ═════════════════════════════════════════════════════════════════════

R_VENUE_TEXT_ABSENT = "NCAAF_VENUE_RULES_TEXT_NOT_RECORDED"
R_VENUE_WINNER = "NCAAF_VENUE_WINNER_CLAUSE_NOT_THE_CITED_COLLEGE_FOOTBALL_GAME"
R_VENUE_OVERTIME = "NCAAF_VENUE_OVERTIME_CLAUSE_NOT_THE_CITED_ONE"
R_VENUE_TIE = "NCAAF_VENUE_TIE_CLAUSE_NOT_THE_CITED_REVIEW_CLAUSE"
R_VENUE_POSTPONED = "NCAAF_VENUE_POSTPONEMENT_CLAUSE_NOT_THE_CITED_ONE"
R_VENUE_SOURCE = "NCAAF_VENUE_RESULT_SOURCE_CLAUSE_NOT_THE_CITED_ONE"
R_VENUE_UNCITED = "NCAAF_VENUE_TEXT_CARRIES_AN_UNCITED_CLAUSE"
R_NO_TIE_RULE = "NCAAF_NO_TIE_RULE_EVIDENCE_NOT_HELD"
R_BOOK_RULES = "NCAAF_BOOK_RULES_CAPTURE_NOT_HELD"
R_BOOK_PRICES_DRAW = "NCAAF_BOOK_LINE_PRICES_A_DRAW_NOT_THE_TWO_WAY_GAME_LINE"
R_DATE_INCONSISTENT = "NCAAF_FIXTURE_DATE_NOT_CONSISTENT_WITH_THE_VENUE_SLUG"
R_DATE_UNREADABLE = "NCAAF_FIXTURE_DATE_NOT_READABLE_FROM_THE_VENUE_SLUG"

#: The clause refusals, in the order the venue's text states the clauses.
VENUE_CLAUSE_REFUSALS = (R_VENUE_WINNER, R_VENUE_OVERTIME, R_VENUE_TIE,
                         R_VENUE_POSTPONED, R_VENUE_SOURCE, R_VENUE_UNCITED)
REFUSALS = ((R_VENUE_TEXT_ABSENT,) + VENUE_CLAUSE_REFUSALS
            + (R_NO_TIE_RULE, R_BOOK_RULES, R_BOOK_PRICES_DRAW,
               R_DATE_INCONSISTENT, R_DATE_UNREADABLE))

#: THE STRICT POLICY'S PRECISE CODES. Derek requires every condition to pay
#: the same on both sides; these are the conditions where the cited texts do
#: not, each its own code, so the strict refusal names the clause instead of
#: a generic "settlement not supported".
S_POSTPONEMENT = "NCAAF_STRICT_POSTPONEMENT_PAYOUTS_DIFFER"
S_SUSPENSION = "NCAAF_STRICT_SUSPENSION_PAYOUTS_DIFFER"
S_TIED_RESULT = "NCAAF_STRICT_TIED_RESULT_VENUE_PAYOUT_NOT_STATED"
S_FORFEIT = "NCAAF_STRICT_FORFEIT_PAYOUTS_DIFFER"
S_VENUE_CHANGE = "NCAAF_STRICT_VENUE_CHANGE_VENUE_PAYOUT_NOT_STATED"
STRICT_CODES = (S_POSTPONEMENT, S_SUSPENSION, S_TIED_RESULT, S_FORFEIT,
                S_VENUE_CHANGE)

# The payouts are keyed `book_payout` / `venue_payout` (never a bare
# "venue": that key names a TRADING VENUE across the package, and
# tests/test_the_venue_identifier_reaches_the_position_model.py refuses a
# payout sentence spelled as one).
STRICT_POLICY_MISSING = (
    {"condition": "POSTPONED_OR_NOT_RESCHEDULED", "code": S_POSTPONEMENT,
     "book_payout": "VOID if not started within 12 h of the original time",
     "venue_payout": ("graded on a game rescheduled within two weeks; "
                      "otherwise the last fair market price S in [0, 1]"),
     "book_quote": Q_BOOK_NOT_STARTED, "venue_quote": Q_VENUE_POSTPONED,
     "what_would_close_it": ("nothing in either document: the payouts "
                             "differ. Only a policy that conditions on "
                             "ordinary completion and discloses this state "
                             "(the completed-game policy) can use the book's "
                             "price")},
    {"condition": "SUSPENDED_CALLED_OR_STOPPED_EARLY", "code": S_SUSPENSION,
     "book_payout": ("< 55 min and not completed in 12 h: VOID; >= 55 min "
                     "and not resumed in 12 h: the score at the suspension "
                     "is final"),
     "venue_payout": ("the last fair market price when not rescheduled within "
                      "two weeks; no payout stated for a game resumed or made "
                      "official"),
     "book_quote": Q_BOOK_SUSPENDED, "venue_quote": Q_VENUE_POSTPONED,
     "what_would_close_it": ("the venue's per-condition rule for a suspended "
                             "college game (its text does not state one)")},
    {"condition": "TIED_RESULT_DECLARED_WITHOUT_AN_OFFICIAL_WINNER",
     "code": S_TIED_RESULT,
     "book_payout": "VOID (no draw price offered: bets on both teams voided)",
     "venue_payout": ("NOT STATED: the market 'will not resolve automatically "
                      "and will be reviewed against the official governing-"
                      "body result'"),
     "book_quote": Q_BOOK_TIE, "venue_quote": Q_VENUE_TIE_REVIEW,
     "what_would_close_it": ("the venue's stated payout for a reviewed tied "
                             "result; its text promises only a review")},
    {"condition": "FORFEIT", "code": S_FORFEIT,
     "book_payout": ("VOID, regardless of how the governing body scores the "
                     "forfeit"),
     "venue_payout": ("the outcome is sourced from the governing body; no "
                      "separate forfeit payout is stated"),
     "book_quote": Q_BOOK_FORFEIT, "venue_quote": Q_VENUE_SOURCE,
     "what_would_close_it": ("a venue clause voiding a forfeited game, or the "
                             "book grading one by the governing body's score "
                             "-- neither is stated")},
    {"condition": "VENUE_CHANGED", "code": S_VENUE_CHANGE,
     "book_payout": "VOID (General Rules, unless the sport rules say otherwise)",
     "venue_payout": "not stated",
     "book_quote": Q_BOOK_VENUE_CHANGED, "venue_quote": None,
     "what_would_close_it": "a venue clause for a relocated game"},
)


# ═════════════════════════════════════════════════════════════════════
# THE VENUE'S TEXT, READ CLAUSE BY CLAUSE
# ═════════════════════════════════════════════════════════════════════
#
# WHY EXACT CLAUSES AND NOT KEYWORDS. The NFL review found that a keyword
# reading let a contradicting tie clause APPENDED to the cited text through
# the real paper pass. So nothing here searches for words: each cited fixed
# clause must occur EXACTLY ONCE (lower-cased, whitespace collapsed), the
# winner clause must be the cited sentence with only the game and the date
# varying, and once those are removed NOTHING may remain. A variant wording,
# a second tie clause, an extra sentence of any kind -- each is a named
# refusal, because the payout reading below is a reading of THESE clauses.
#
# The fixed clauses are removed as whole substrings before the winner clause
# is read, so a period inside a school's name ("St. Thomas") cannot split it.

_FIXED = (("overtime", Q_VENUE_OVERTIME, R_VENUE_OVERTIME),
          ("tie_review", Q_VENUE_TIE_REVIEW, R_VENUE_TIE),
          ("postponed", Q_VENUE_POSTPONED, R_VENUE_POSTPONED),
          ("result_source", Q_VENUE_SOURCE, R_VENUE_SOURCE))
_WINNER = re.compile(
    r"^this market will settle to the winner of the (?P<game>.+? vs .+?) "
    r"college football game scheduled for (?P<mon>[a-z]{3,9})\.? "
    r"(?P<day>\d{1,2}), (?P<year>\d{4})\.")
_TIE_WORD = re.compile(r"\b(?:ties?|tied|tying|draws?|drawn)\b")


def _flat(prose) -> str:
    return " ".join(str(prose or "").split())


def venue_clauses(prose) -> dict:
    """{"ok": bool, "refusals": [...], "clauses": {...}} for a cfb contract's
    own rules text. ok only when the text is EXACTLY the five cited clauses
    (the game and the date free). Pure."""
    flat = _flat(prose)
    low = flat.lower()
    out = {"ok": False, "refusals": [], "clauses": {}, "why": []}
    if not low:
        out["refusals"] = [R_VENUE_TEXT_ABSENT]
        out["why"] = ["no venue rules text is recorded for this contract"]
        return out
    rest = low
    for name, quote, code in _FIXED:
        q = quote.lower()
        n = low.count(q)
        out["clauses"][name] = {"stated": n, "quote": quote}
        if n != 1:
            out["refusals"].append(code)
            out["why"].append("the cited %s clause is stated %d time(s), not "
                              "once" % (name, n))
        rest = rest.replace(q, "\n")
    # What is left between the removed clauses: the winner clause is read as
    # a PREFIX of its piece (so a variant sentence that follows it is not
    # mistaken for a missing winner clause), and any tail is uncited text.
    winners, others = [], []
    for p in (x.strip() for x in rest.split("\n")):
        if not p:
            continue
        m = _WINNER.match(p)
        if m:
            winners.append(m.group("game"))
            tail = p[m.end():].strip()
            if tail:
                others.append(tail)
        else:
            others.append(p)
    out["clauses"]["winner"] = {"stated": len(winners),
                                "quote": Q_VENUE_WINNER,
                                "game": winners[0] if len(winners) == 1
                                else None}
    if len(winners) != 1:
        out["refusals"].append(R_VENUE_WINNER)
        out["why"].append(
            "the text states the cited winner-of-a-named-College-Football-"
            "game clause %d time(s), not once" % len(winners))
    if others:
        out["refusals"].append(R_VENUE_UNCITED)
        out["uncited"] = others
        ties = [o for o in others if _TIE_WORD.search(o)]
        if ties:
            # A SECOND TIE CLAUSE contradicts or qualifies the cited review
            # clause (the NFL review's appended-clause case): the tie reading
            # is then not the cited one, and is named as such.
            out["uncited_tie_text"] = ties
            if R_VENUE_TIE not in out["refusals"]:
                out["refusals"].append(R_VENUE_TIE)
        out["why"].append("the text carries %d clause(s) outside the cited "
                          "five: %r" % (len(others), others))
    # the refusals in the order the venue states the clauses
    out["refusals"] = [r for r in VENUE_CLAUSE_REFUSALS
                       if r in out["refusals"]]
    out["ok"] = not out["refusals"]
    return out


def _draw_named(names) -> bool:
    return any(str(n).strip().lower() in ("draw", "tie", "x")
               for n in (names or ()))


def no_tie_rule(evidence=None) -> dict:
    """{"held": bool, ...}: the cited NCAA rule that a game tied after
    regulation is played to a winner, or why it is not held. Pure."""
    ev = NO_TIE_RULE_EVIDENCE if evidence is None else evidence
    need = ("rule_quote", "source_url", "retrieved_at", "run_id", "sha256")
    missing = [k for k in need if not (ev or {}).get(k)]
    if missing:
        return {"held": False, "refusal": R_NO_TIE_RULE,
                "why": "the no-tie rule evidence lacks %s" % missing}
    return {"held": True, "refusal": None, "rule": ev.get("rule"),
            "rule_quote": ev["rule_quote"], "source": ev.get("source"),
            "source_url": ev["source_url"], "retrieved_at": ev["retrieved_at"],
            "run_id": ev["run_id"], "sha256": ev["sha256"],
            "scope": ev.get("scope")}


def book_rules_held(captures=None, quotes=None) -> dict:
    """{"held": bool, ...}: the Pinnacle capture this module cites is on
    record (url, sha256, a retrieval with its run) and every sentence it
    rests on is non-empty. Pure."""
    caps = PINNACLE_CAPTURES if captures is None else captures
    qs = BOOK_QUOTES if quotes is None else quotes
    good = [c for c in (caps or ()) if c.get("sha256") == PINNACLE_PAGE_SHA256
            and c.get("retrieved_at") and c.get("run_id")]
    if not good or not qs or not all(str(q or "").strip() for q in qs):
        return {"held": False, "refusal": R_BOOK_RULES,
                "why": ("no Pinnacle rules capture with the cited sha256 and "
                        "a recorded retrieval, or a cited sentence is empty")}
    return {"held": True, "refusal": None, "captures": len(good),
            "latest": good[-1]["retrieved_at"]}


# ═════════════════════════════════════════════════════════════════════
# THE CONVERSION: BOOK'S TWO-WAY PRICE -> THE VENUE CONTRACT'S VALUE
# ═════════════════════════════════════════════════════════════════════

P_IS_EQUIVALENT = ("VENUE_PAYOUT_EQUALS_THE_BOOK_TWO_WAY_PRICE_A_COMPLETED_NCAA_"
                   "GAME_CANNOT_END_TIED")


def is_ncaaf(sport_family, league) -> bool:
    return (str(sport_family or "").strip().lower() == "football"
            and str(league or "").strip().lower() == LEAGUE)


def convert(p_book, *, sport_family, league, venue_rules_text,
            book_outcome_names=None, evidence=None) -> dict:
    """THE CONVERSION THE COMPLETED-GAME POLICY APPLIES BEFORE ANY EDGE.

    Returns {"applies": bool, "p": float | None, "refusal": str | None, ...}.
    Not an NCAAF money line: applies False and `p` unchanged. NCAAF: the
    book's line must be the two-way game line (no Draw priced), the venue's
    text must be exactly the cited clauses, the book capture and the no-tie
    rule must be held; then `p` is the book's number, UNCHANGED, because a
    completed NCAA game cannot end tied (the derivation is on the record).
    Any miss is a named refusal with `p` None."""
    if not is_ncaaf(sport_family, league):
        return {"applies": False, "p": p_book, "refusal": None,
                "why": "no NCAAF conversion outside the college money line"}
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
            "money line is a regulation (three-way) market, a different event "
            "from the venue's overtime-included contract"))
        return out
    vc = venue_clauses(venue_rules_text)
    out["venue_clauses"] = {k: vc[k] for k in ("ok", "refusals")}
    if not vc["ok"]:
        out.update(refusal=vc["refusals"][0], why="; ".join(vc["why"]))
        return out
    bk = book_rules_held()
    if not bk["held"]:
        out.update(refusal=bk["refusal"], why=bk["why"])
        return out
    nt = no_tie_rule(evidence)
    out["no_tie_rule"] = nt
    if not nt["held"]:
        out.update(refusal=nt["refusal"], why=nt["why"])
        return out
    out.update(
        p=float(p_book), p_is=P_IS_EQUIVALENT, tie_probability_completed=0.0,
        formula=("p_venue = (1 - t) * p_book + 0 * t with t = P(tie | "
                 "completed) = 0 by the cited rule, so p_venue = p_book"),
        derivation=(
            "the book voids a draw, so its de-vigged two-way price is P(win | "
            "no tie); a completed NCAA game is played to a winner, so no tie "
            "is removed from the sample and P(win | no tie) = P(win | "
            "completed); the venue pays 1 on that win and 0 on that loss in "
            "every completed game"))
    return out


# ═════════════════════════════════════════════════════════════════════
# THE FIXTURE DATE: VENUE SLUG == VENUE PROSE == KICKOFF ON THE ET DAY
# ═════════════════════════════════════════════════════════════════════
#
# The venue dates a cfb contract by the America/New_York day of the game, as
# it does an NFL contract: the captured Fresno State at Washington State game
# kicks off 2026-10-04T01:30Z (21:30 ET on Oct 3) and is
# aec-cfb-frest-washst-2026-10-03, "scheduled for Oct 3, 2026". Production
# agrees on every listed row: research-sql run 37241503567 (N7) read 171 cfb
# money-line catalogue rows (86 contracts, ET days 2026-10-03 .. 10-07) and
# the slug date equals the kickoff's ET day on 171 of 171 (the UTC day on
# only 147). The reading is the NFL module's (pure); only the codes differ.

_DATE_CODES = {_NFL.R_DATE_INCONSISTENT: R_DATE_INCONSISTENT,
               _NFL.R_DATE_UNREADABLE: R_DATE_UNREADABLE}


def fixture_date(*, slug, venue_rules_text=None, kickoff_epoch=None) -> dict:
    """The cfb contract's event date and whether its three readings agree."""
    got = _NFL.fixture_date(slug=slug, venue_rules_text=venue_rules_text,
                            kickoff_epoch=kickoff_epoch)
    if got.get("refusal"):
        got["refusal"] = _DATE_CODES.get(got["refusal"], R_DATE_INCONSISTENT)
    return got


def league_of_slug(slug) -> str | None:
    """'cfb' for aec-cfb-..., the venue's league token, else None. Pure."""
    return _NFL.league_of_slug(slug)


# ═════════════════════════════════════════════════════════════════════
# THE STATE TABLE (payouts, citations, class)
# ═════════════════════════════════════════════════════════════════════

ORDINARY = "ORDINARY_COMPLETION_IN_THE_CONDITIONAL_EV"
UNREACHABLE = "UNREACHABLE_IN_A_COMPLETED_GAME_BY_THE_CITED_RULE"
EXCEPTIONAL = "EXCEPTIONAL_DISCLOSED_PROBABILITY_UNMEASURED"


def _states():
    nt = NO_TIE_RULE_EVIDENCE or {}
    return (
        {"state": "REGULATION_WIN", "class": ORDINARY,
         "venue_payout": "1 on the winner's side, 0 on the other",
         "book_payout": "the bet on the winner wins at its price, the other "
                        "loses",
         "verdict": "SAME_EVENT_SAME_DIRECTION",
         "venue_cite": _venue_cite(Q_VENUE_WINNER),
         "book_cite": _book_cite(Q_BOOK_OVERTIME, "American Football")},
        {"state": "OVERTIME_WIN", "class": ORDINARY,
         "venue_payout": "1 on the winner's side, 0 on the other",
         "book_payout": "the bet on the winner wins at its price, the other "
                        "loses",
         "verdict": "SAME_EVENT_SAME_DIRECTION",
         "venue_cite": _venue_cite(Q_VENUE_OVERTIME),
         "book_cite": _book_cite(Q_BOOK_OVERTIME, "American Football")},
        {"state": "RESULT_SOURCE", "class": ORDINARY,
         "venue_payout": "the governing body's result",
         "book_payout": ("the governing body's final determination on the "
                         "date of completion"),
         "verdict": "SAME_RESULT_SOURCE",
         "venue_cite": _venue_cite(Q_VENUE_SOURCE),
         "book_cite": _book_cite(Q_BOOK_RESULT, "General Rules")},
        {"state": "TIE_AFTER_OVERTIME_IN_A_COMPLETED_GAME",
         "class": UNREACHABLE,
         "venue_payout": "not resolved automatically; reviewed (not stated)",
         "book_payout": "VOID (stake returned)",
         "verdict": "UNREACHABLE_NO_PAYOUT_TO_COMPARE",
         "rule_cite": {k: nt.get(k) for k in ("rule_quote", "source",
                                              "source_url", "retrieved_at",
                                              "run_id", "sha256")},
         "consequence": ("P(tie | completed) = 0 by the cited rule: the "
                         "book's two-way price P(win | no tie) equals P(win | "
                         "completed), the contract's value, unconverted")},
        {"state": "TIED_RESULT_DECLARED_WITHOUT_AN_OFFICIAL_WINNER",
         "class": EXCEPTIONAL,
         "venue_payout": "NOT STATED (reviewed against the governing-body "
                         "result)",
         "book_payout": ("a game stopped level is a suspension: VOID (< 55 "
                         "min), or the score at the suspension, a draw, which "
                         "voids (>= 55 min)"),
         "verdict": "VENUE_PAYOUT_NOT_STATED",
         "venue_cite": _venue_cite(Q_VENUE_TIE_REVIEW),
         "book_cite": _book_cite(Q_BOOK_TIE, "General Rules")},
        {"state": "POSTPONED_RESCHEDULED_WITHIN_TWO_WEEKS",
         "class": EXCEPTIONAL,
         "venue_payout": ("graded on the rescheduled game (the price clause "
                          "fires only when NOT rescheduled within two weeks; "
                          "the venue does not state this payout in its own "
                          "words)"),
         "book_payout": "VOID when not started within 12 h of the original "
                        "time",
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
                         "and not resumed in 12 h: the score at the "
                         "suspension"),
         "verdict": "DIFFERENT_PAYOUT",
         "venue_cite": _venue_cite(Q_VENUE_POSTPONED),
         "book_cite": _book_cite(Q_BOOK_SUSPENDED, "American Football")},
        {"state": "FORFEIT", "class": EXCEPTIONAL,
         "venue_payout": ("the governing body's result (no separate forfeit "
                          "payout stated)"),
         "book_payout": "VOID regardless of the governing body's scoring",
         "verdict": "DIFFERENT_PAYOUT",
         "venue_cite": _venue_cite(Q_VENUE_SOURCE),
         "book_cite": _book_cite(Q_BOOK_FORFEIT, "General Rules")},
        {"state": "RESULT_OVERTURNED_AFTER_THE_GAME_DATE",
         "class": EXCEPTIONAL,
         "venue_payout": "not stated",
         "book_payout": "not recognised (the result on the completion date "
                        "stands)",
         "verdict": "VENUE_SILENT",
         "book_cite": _book_cite(Q_BOOK_RESULT, "General Rules")},
        {"state": "VENUE_CHANGED", "class": EXCEPTIONAL,
         "venue_payout": "not stated",
         "book_payout": "VOID (General Rules, unless the sport rules say "
                        "otherwise)",
         "verdict": "VENUE_SILENT",
         "book_cite": _book_cite(Q_BOOK_VENUE_CHANGED, "General Rules")},
    )


def states_record() -> dict:
    """The whole comparison for a decision record. Pure."""
    nt = no_tie_rule()
    return {"version": VERSION, "states": [dict(s) for s in _states()],
            "no_tie_rule_held": nt["held"],
            "exceptional_probability": "UNMEASURED",
            "exceptional_note": (
                "postponement, suspension, a tied result declared without a "
                "winner, a forfeit, an overturned result and a venue change "
                "pay differently on the two sides or are not stated by the "
                "venue; their NCAAF frequency is not measured here, is never "
                "set to zero and is not part of the completed-game EV"),
            "strict_policy_missing": [dict(m) for m in STRICT_POLICY_MISSING]}


def strict_policy_codes(venue_rules_text) -> list:
    """THE STRICT POLICY'S PRECISE REFUSALS for one cfb contract, in order:
    first any clause of its own text that is not the cited one (the payout
    comparison below is a comparison of THOSE clauses), then every condition
    the cited texts pay differently or leave unstated. Never empty. Pure."""
    vc = venue_clauses(venue_rules_text)
    return list(vc["refusals"]) + list(STRICT_CODES)


def describe() -> dict:
    nt = no_tie_rule()
    return {"version": VERSION, "league": LEAGUE,
            "provider_key": PROVIDER_KEY,
            "no_tie_rule_held": nt["held"],
            "no_tie_rule_source": (NO_TIE_RULE_EVIDENCE or {}).get(
                "source_url"),
            "refusals": list(REFUSALS),
            "strict_codes": list(STRICT_CODES),
            "states": [s["state"] for s in _states()]}
