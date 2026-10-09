"""THE MARKET PLANE'S SETTLEMENT STATE, PER ACTIVE CONTRACT, FROM EVIDENCE ONLY.

MARKET DATA / RESEARCH ONLY. Decision-time `bettor_venue_settlement.attest`
(with `bettor_settlement_terms.compare_prose`'s condition -> payout
comparison) REMAINS THE CONTROLLING COMPATIBILITY DECISION FOR TRADING. This
module only says, for coverage, which contracts that decision -- or the same
comparison run on the contract's captured CURRENT rules text -- has proven,
and names precisely why the rest are not proven. Nothing here admits,
sizes, prices or orders anything.

Exactly one of five states:

  SETTLEMENT_PROVEN_COMPATIBLE
      basis DECISION_ATTEST: the latest decision valuation's settlement
      comparison is COMPATIBLE (and, where both are recorded, the rules
      fingerprint it was decided on is the one captured now); or
      basis RULES_TERMS_COMPARISON (a contract never valued):
      compare_prose(sport_family, "h2h", venue_prose=<captured rules text>,
      extra_book_terms=bettor_venue_settlement._legacy_book_terms(fam)) --
      the exact call attest makes -- returns COMPATIBLE with the bookmaker's
      terms HELD, and no tie clause the terms comparison does not cover.
  SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED
      basis DECISION_PRICED_SETTLEMENT_DIFFERENCE: the latest paper decision
      on the contract recorded bettor_settlement_difference_policy.
      eligibility as eligible AND the policy's own price (marker, id, CURRENT
      version, p) -- the priced branch of bettor_capital_eligibility.
      settlement_resolved, restated (no capital module is imported).
      A never-valued contract has no book probability to price and no
      completed-game match, so SDP.eligibility is run on its comparison and
      refuses by its own name: a price is never invented here.
  SETTLEMENT_RULE_EVIDENCE_CONFLICT
      the captured rule evidence is CONFLICT (an explicit structured rule the
      parsed text contradicts) or the venue's own text gives one condition
      two payouts. Fails closed, over any decision verdict.
  EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE
      ONLY when the venue's own listing was read and carries no rules field.
  MAPPED_BUT_SETTLEMENT_NOT_PROVEN
      everything else, with a precise why (VENUE_RULES_NOT_CAPTURED,
      VENUE_RULES_SILENT_ON:<conditions>, BOOKMAKER_TERMS_NOT_HELD:<...>,
      INCOMPATIBLE_NOT_PRICEABLE:<conditions>:<policy refusal>,
      RULES_CHANGED_SINCE_DECISION_ATTEST, ...). Silence is never agreement.
"""
from __future__ import annotations

import re

COMPATIBLE = "SETTLEMENT_PROVEN_COMPATIBLE"
DIFFERENT_BUT_PRICED = "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED"
NOT_PROVEN = "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
CONFLICT = "SETTLEMENT_RULE_EVIDENCE_CONFLICT"
EXTERNAL = "EXTERNAL_SETTLEMENT_DATA_UNAVAILABLE"
STATES = (COMPATIBLE, DIFFERENT_BUT_PRICED, NOT_PROVEN, CONFLICT, EXTERNAL)
PROVEN_STATES = (COMPATIBLE, DIFFERENT_BUT_PRICED)

BASIS_DECISION_ATTEST = "DECISION_ATTEST"
BASIS_DECISION_PRICED = "DECISION_PRICED_SETTLEMENT_DIFFERENCE"
BASIS_RULES_TERMS = "RULES_TERMS_COMPARISON"
BASIS_RULE_EVIDENCE = "RULE_EVIDENCE"
BASIS_NONE = "NO_EVIDENCE"

R_NO_COMPARISON = "NO_SETTLEMENT_COMPARISON_RECORDED"
R_VENUE_RULES_NOT_CAPTURED = "VENUE_RULES_NOT_CAPTURED"
R_VENUE_PUBLISHES_NO_RULES = "VENUE_PUBLISHES_NO_RULES_TEXT_FOR_THIS_CONTRACT"
R_VENUE_RULES_SILENT_ON = "VENUE_RULES_SILENT_ON"
R_BOOKMAKER_TERMS_NOT_HELD = "BOOKMAKER_TERMS_NOT_HELD"
R_INCOMPATIBLE_NOT_PRICEABLE = "INCOMPATIBLE_NOT_PRICEABLE"
R_RULES_CHANGED_SINCE_DECISION = "RULES_CHANGED_SINCE_DECISION_ATTEST"
R_TIE_RULE_NOT_COMPARED = "TIE_RULE_NOT_COVERED_BY_THE_TERMS_COMPARISON"
R_KALSHI_NOT_MAPPED = "KALSHI_CONTRACT_NOT_MAPPED_TO_A_BETTOR_FAMILY"
R_RULE_EVIDENCE_CONFLICT = "RULE_EVIDENCE_CONFLICT"
R_VENUE_SELF_CONTRADICTORY = "VENUE_RULES_SELF_CONTRADICTORY"

#: (RC6, lane D2) a family whose book terms ARE captured
#: (bettor_settlement_terms.BOOK_TERMS) but are not admissible for THIS
#: fixture until its quote context / competition phase / game format is
#: established -- book_terms() withholds them by design. Not "not held".
R_BOOK_TERMS_SCOPE = "BOOK_TERMS_SCOPE_NOT_ESTABLISHED"
#: (RC6) a full-game line contract whose venue text grades the ordinarily
#: completed game exactly as the book's cited terms do, and whose
#: postponement / suspension / short-game terms differ: the difference the
#: priced settlement-difference policy refuses for a non-money-line
#: (bettor_settlement_difference_policy.R_LINE). Same code the line lane
#: refuses with (bettor_market_family.R_EXCEPTIONAL_DIFFER).
R_LINE_EXCEPTIONAL_DIFFER = "LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER"
#: (RC6) ... and one whose venue text states no rule for those states at all
R_LINE_VENUE_EXCEPTIONAL_SILENT = "LINE_VENUE_TEXT_STATES_NO_EXCEPTIONAL_RULE"

AUTHORITY_NOTE = ("coverage evidence only: decision-time bettor_venue_"
                  "settlement.attest remains the authority for trading")

#: the families whose money line has captured bookmaker terms
#: (bettor_settlement_terms.BOOK_TERMS); every other family / market is
#: BOOKMAKER_TERMS_NOT_HELD for a never-valued contract
H2H_FAMILIES = ("baseball", "basketball", "football", "hockey", "soccer")
#: compare_prose results by (rules fingerprint, family, league): a contract's
#: text is compared once per process, not once per coverage pass
_TERMS_CACHE: dict = {}
_TERMS_CACHE_MAX = 250_000


def _short(xs, n=8) -> str:
    xs = [str(x) for x in (xs or [])]
    return ",".join(xs[:n]) + (",+%d" % (len(xs) - n) if len(xs) > n else "")


def _rules_evidence(rules: dict | None) -> dict:
    r = dict(rules or {})
    ev = r.get("evidence") or {}
    if isinstance(ev, str):
        import json
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = {}
    return {"rules_sha256": r.get("rules_sha256"),
            "rules_field": r.get("rules_field"),
            "rules_published": r.get("rules_published"),
            "parse_status": r.get("parse_status"),
            "parser_version": r.get("parser_version"),
            "source": r.get("source"),
            "matched": list(ev.get("matched") or []),
            "verification_sources": list(ev.get("verification_sources")
                                         or []),
            "special_conditions": list(ev.get("special_conditions") or []),
            "parsed_settlement": dict(ev.get("settlement") or {}),
            "conflicts": dict(ev.get("conflicts") or {})}


def kalshi_mapped(contract: dict) -> bool:
    """A Kalshi registry row the Kalshi ontology mapped: its ontology names
    no gap, and it has a sport and a family (market_plane.populate.
    kalshi_contract_row). Before RC6 no Kalshi row was mapped."""
    c = dict(contract or {})
    ont = c.get("ontology")
    if isinstance(ont, str):
        import json
        try:
            ont = json.loads(ont)
        except ValueError:
            ont = None
    if not isinstance(ont, dict) or "gaps" not in ont or ont.get("gaps"):
        return False
    return bool(c.get("sport")) and bool(c.get("family"))


def h2h_family(contract: dict) -> str | None:
    """The bookmaker-terms family of a full-event WINNER contract, else None
    (a line, period or prop market has no captured money-line terms)."""
    sp = str(contract.get("sport") or "").lower()
    if sp in H2H_FAMILIES and str(contract.get("family") or "") == "WINNER" \
            and str(contract.get("period") or "") == "FULL_EVENT":
        return sp
    return None


def terms_comparison(*, sport_family, league, rules_text, rules_sha256):
    """compare_prose exactly as attest calls it, cached by fingerprint."""
    key = (rules_sha256, sport_family, league)
    if rules_sha256 and key in _TERMS_CACHE:
        return _TERMS_CACHE[key]
    from .. import bettor_settlement_terms as ST
    from .. import bettor_venue_settlement as V
    cmp_ = ST.compare_prose(sport_family=sport_family, market="h2h",
                            venue_prose=rules_text or "",
                            extra_book_terms=V._legacy_book_terms(
                                sport_family))
    slim = {"verdict": cmp_.get("verdict"),
            "book_terms_held": bool(cmp_.get("book_terms_held")),
            "per_condition": {c: {k: r.get(k) for k in (
                "verdict", "book_payout", "venue_payout")}
                for c, r in (cmp_.get("per_condition") or {}).items()},
            "mismatched_conditions": list(cmp_.get("mismatched_conditions")
                                          or []),
            "unstated_conditions": list(cmp_.get("unstated_conditions")
                                        or []),
            "venue_self_contradictory": list(
                cmp_.get("venue_self_contradictory") or []),
            "book_side_absent_refusals": list(
                cmp_.get("book_side_absent_refusals") or []),
            "refusal": cmp_.get("refusal")}
    if len(_TERMS_CACHE) > _TERMS_CACHE_MAX:
        _TERMS_CACHE.clear()
    if rules_sha256:
        _TERMS_CACHE[key] = slim
    return slim


# ── RC6 (lane D2): FULL-GAME LINE FAMILIES, READ AS THE LINE LANE READS THEM
#
# THE DEFECT. A never-valued spread / total / team total fell through to
# `h2h_family() is None` and was reported BOOKMAKER_TERMS_NOT_HELD:<sport>/
# <family>/<period>. For football, hockey, basketball and baseball that is
# false: bettor_market_family.EQUIVALENCE holds the book's grading of those
# families, cited verbatim from the captured rules page (sha256 63d64321...),
# and the line lane values them on it. Production (research-sql run
# 37870767576 / 37871119335): 11,700 active full-game line contracts carried
# the label -- football spread 4,141, total 2,630, team total 2,539, hockey
# team total 404 / total 271 / spread 193, basketball total 62 / spread 49,
# baseball 21 -- while 70 VALUED ones of the same types already read
# SETTLEMENT_VERDICT:INCOMPATIBLE_EXCEPTIONAL_TERMS from the decision record.
#
# THE READING. The contract's OWN captured rules text against its family's
# captured venue wording (the line lane's `prove`, minus the team binding,
# which is decision-time identity, not settlement): the family statement,
# every required phrase, no contradicting phrase, the text's line equal to
# the slug's, a half-point line (no push). Then the exceptional states -- the
# postponement / suspension / short-game terms the two sides settle
# differently, disclosed per family in EQUIVALENCE -- and the venue clause the
# text itself states for them. The state is NEVER proven here: the priced
# settlement-difference policy refuses every non-money-line by its own code,
# so the why names the difference and that refusal. A family the module does
# not prove (soccer / tennis lines: the book's market rules or sport section
# not captured; basketball team totals: no venue capture) keeps its precise
# refusal; only the book-side ones keep the BOOKMAKER_TERMS_NOT_HELD prefix.
LINE_TERMS_VERSION = "MARKET_PLANE_LINE_TERMS_V1"
_LINE_CACHE: dict = {}
_LINE_CACHE_MAX = 250_000
_VENUE_X_CLAUSES = (
    ("LAST_FAIR_MARKET_PRICE_FLAGGED_FOR_MANUAL_SETTLEMENT",
     r"\bflagged for manual last[\s-]+fair[\s-]+market[\s-]+price\b"),
    ("LAST_FAIR_MARKET_PRICE",
     r"\blast[\s-]+fair[\s-]+market[\s-]+price\b"),
)


# ── PERIOD LINES: the same reading, one table of cited period terms ──────
#
# The book's period rule is a GENERAL rule, captured verbatim on the same
# page (tests/fixtures/pinnacle_line_rules_2026_10_04.json, general_rules):
# "Bets on a specific period count only the scoring in that period, and are
# unaffected by what happens in prior or subsequent periods. ... Any periods
# that have been completed will have action." -- and the American Football
# section names the one period that includes overtime: "Bets on the Game and
# 2nd Half-periods include points scored in overtime." The venue's period
# wording was read off production rows (research-sql runs 37870767576 T1 /
# T2 and 37871400151 F1): its football 1st-half / quarter totals and team
# totals say "Only points recorded in regulation during that period count;
# overtime is not included", its 2nd-half markets "Overtime is included if
# played", its hockey 3rd-period markets "Overtime and any shootout are not
# included if played". So, per period, on the ORDINARILY COMPLETED game:
#
#   1st half, Q1-Q3, P1, P2   no overtime can fall inside the period: both
#                             sides grade the period's own scoring
#   2nd half (football)       BOTH include overtime -- the venue text must
#                             say so
#   Q4 (football), P3         the book's period-only rule EXCLUDES overtime
#                             -- the venue text must say so; a Q4 spread
#                             whose text says nothing about overtime is NOT
#                             read as excluding it (refused, by name)
#
# and the period's exceptional state (the period not completed, or the game
# postponed before it is) is settled differently -- the book: a completed
# period has action, the full fixture voids; the venue: manual last-fair-
# market-price settlement -- so the reading is never PROVEN. Soccer halves
# stay BOOKMAKER_TERMS_NOT_HELD (the book's Soccer Market Rules section,
# which outranks its sport rules, is outside the capture); period WINNERS
# are not claimed (the book's tie payout depends on whether its own market
# offers a draw -- General Rules -- which no rule text establishes).
_PW = {"FIRST_HALF": "first half", "SECOND_HALF": "second half",
       "Q1": "first quarter", "Q2": "second quarter", "Q3": "third quarter",
       "Q4": "fourth quarter", "P1": "first period", "P2": "second period",
       "P3": "third period"}
_P_NUM = r"(?P<line>[-+]?\d+(?:\.\d+)?)"
_P_OT_IN = r"\bovertime is included if played\b"
_P_OT_OUT = r"\bovertime is not included\b"
_P_HK_OT_OUT = r"\bovertime and any shootout are not included\b"
_P_TEMPLATES = {
    ("football", "spread"): (r"\bwill settle to yes if (?:the )?(?P<team>.+?) "
                             r"covers? an? " + _P_NUM + r" point spread in "
                             r"the {pw} of the\b"),
    # two venue wordings, both read off production rows: the college board's
    # ("1st Quarter Point Total settles Over if A and B combine for more
    # than 10.5 points in the first quarter of the ...", 37871400151 F1) and
    # the NFL board's ("This market will settle to Yes if A and B combine
    # for over 0.5 points in the first quarter of the ...", 37872672403 W2)
    ("football", "total"): (r"\b(?:point total settles over if .+? combine "
                            r"for more than|will settle to yes if .+? "
                            r"combine for over) " + _P_NUM + r" points in "
                            r"the {pw} of the\b"),
    ("football", "team_total"): (r"\bteam (?:1st|2nd) half point total "
                                 r"settles over if (?:the )?(?P<team>.+?) "
                                 r"scores? more than " + _P_NUM + r" points "
                                 r"in the {pw} of the\b"),
    ("hockey", "spread"): (r"\bwill settle to yes if (?:the )?(?P<team>.+?) "
                           r"covers? an? " + _P_NUM + r" goal spread in the "
                           r"{pw} of the\b"),
    ("hockey", "total"): (r"\bwill settle to yes if .+? combine for over "
                          + _P_NUM + r" goals during the {pw} in the\b"),
}
#: (sport, period) -> the overtime phrase the venue text must state
_P_OT_REQUIRED = {("football", "SECOND_HALF"): _P_OT_IN,
                  ("football", "Q4"): _P_OT_OUT,
                  ("hockey", "P3"): _P_HK_OT_OUT}
_P_OT_CONFLICT = {("football", "SECOND_HALF"): (_P_OT_OUT,),
                  ("football", "Q4"): (_P_OT_IN,),
                  ("hockey", "P3"): (r"\bovertime is included if played\b",)}
#: the book's cited sentences, by key (each asserted verbatim in the capture)
PERIOD_BOOK = {
    "general_period": ("general_rules",
                       "Bets on a specific period count only the scoring in "
                       "that period, and are unaffected by what happens in "
                       "prior or subsequent periods."),
    "general_period_action": ("general_rules",
                              "Any periods that have been completed will "
                              "have action."),
    "af_overtime": ("american_football",
                    "Bets on the Game and 2nd Half-periods include points "
                    "scored in overtime."),
    "hk_overtime": ("hockey",
                    "Unless otherwise specified, Game-period bets include "
                    "overtime and penalty shootouts."),
}
#: venue sportsMarketType -> (sport, family, period); every type was read
#: carrying the wording above in production (37870767576 T1)
PERIOD_LINE_TYPES = {
    "football_team_first_half_spread": ("football", "spread", "FIRST_HALF"),
    "football_team_second_half_spread": ("football", "spread",
                                         "SECOND_HALF"),
    "football_team_first_quarter_spread": ("football", "spread", "Q1"),
    "football_team_second_quarter_spread": ("football", "spread", "Q2"),
    "football_team_third_quarter_spread": ("football", "spread", "Q3"),
    "football_team_fourth_quarter_spread": ("football", "spread", "Q4"),
    "football_game_first_half_total": ("football", "total", "FIRST_HALF"),
    "football_game_second_half_total": ("football", "total", "SECOND_HALF"),
    "football_game_first_quarter_total": ("football", "total", "Q1"),
    "football_game_second_quarter_total": ("football", "total", "Q2"),
    "football_game_third_quarter_total": ("football", "total", "Q3"),
    "football_game_fourth_quarter_total": ("football", "total", "Q4"),
    "football_team_first_half_total": ("football", "team_total",
                                       "FIRST_HALF"),
    "football_team_second_half_total": ("football", "team_total",
                                        "SECOND_HALF"),
    "hockey_team_first_period_spread": ("hockey", "spread", "P1"),
    "hockey_team_second_period_spread": ("hockey", "spread", "P2"),
    "hockey_team_third_period_spread": ("hockey", "spread", "P3"),
    "hockey_team_first_period_total": ("hockey", "total", "P1"),
    "hockey_team_second_period_total": ("hockey", "total", "P2"),
    "hockey_team_third_period_total": ("hockey", "total", "P3"),
    # the book's Soccer Market Rules section is not captured: named, below
    "soccer_team_first_half_spread": ("soccer", "spread", "FIRST_HALF"),
    "soccer_team_second_half_spread": ("soccer", "spread", "SECOND_HALF"),
    "soccer_team_first_half_total": ("soccer", "total", "FIRST_HALF"),
    "soccer_team_second_half_total": ("soccer", "total", "SECOND_HALF"),
}
PERIOD_EXCEPTIONAL = ("PERIOD_NOT_COMPLETED_OR_POSTPONED_BEFORE_IT_IS",)


def line_family(contract: dict) -> dict | None:
    """{sport, family, sports_type[, segment]} when the contract's venue
    market type is a full-game line type the line lane reads, or a period
    line type of PERIOD_LINE_TYPES, else None. Pure."""
    mt = (contract or {}).get("market_type")
    if not mt:
        return None
    per = PERIOD_LINE_TYPES.get(str(mt).strip().lower())
    if per is not None:
        return {"sport": per[0], "family": per[1], "segment": per[2],
                "sports_type": str(mt).strip().lower()}
    from .. import bettor_market_family as MF
    fam = MF.venue_line_family(mt)
    return None if fam.get("refusal") else fam


def _period_spec(fam: dict) -> dict | None:
    """The period reading's spec, or None when the book side is not held."""
    sport, family, seg = fam["sport"], fam["family"], fam["segment"]
    tpl = _P_TEMPLATES.get((sport, family))
    if tpl is None:
        return None
    book = ["general_period", "general_period_action"] + (
        ["af_overtime"] if sport == "football" else ["hk_overtime"])
    req = _P_OT_REQUIRED.get((sport, seg))
    return {"period": "%s_SCORING_ONLY%s" % (seg, (
                "_INCLUDING_OVERTIME" if req == _P_OT_IN else
                "_EXCLUDING_OVERTIME" if req else "")),
            "statement": tpl.format(pw=_PW[seg]),
            "all_of": (req,) if req else (),
            "none_of": _P_OT_CONFLICT.get((sport, seg), ()),
            "book": book, "exceptional": [{"condition": c}
                                          for c in PERIOD_EXCEPTIONAL]}


def line_key(rules_sha256, fam: dict, contract_id) -> tuple:
    """The cache key of one line reading: the text, the family, the line
    and (for a period line) the period."""
    from .. import bettor_market_family as MF
    return (rules_sha256, fam.get("sport"), fam.get("family"),
            MF._slug_line(contract_id, fam.get("family")),
            fam.get("segment"))


def line_terms(*, contract_id, fam: dict, rules_text, rules_sha256) -> dict:
    """The venue text of one full-game or period line contract read
    against its family's (period's) captured terms. Cached by `line_key`.
    Pure."""
    key = line_key(rules_sha256, fam, contract_id)
    if rules_sha256 and key in _LINE_CACHE:
        return _LINE_CACHE[key]
    from .. import bettor_market_family as MF
    sport, family = fam.get("sport"), fam.get("family")
    out = {"version": LINE_TERMS_VERSION, "sport": sport, "family": family,
           "sports_type": fam.get("sports_type"), "established": False,
           "refusal": None, "line": key[3]}
    if fam.get("segment"):
        out["segment"] = fam["segment"]
        spec = _period_spec(fam)
        st = ({"proven": True} if spec is not None else
              {"proven": False, "refusal": (
                  MF.R_BOOK_MARKET_RULES_NOT_CAPTURED if sport == "soccer"
                  else MF.R_BOOK_SPORT_NOT_CAPTURED),
               "why": "no period terms are captured for %s" % sport})
    else:
        spec = MF.EQUIVALENCE.get((sport, family))
        st = MF.family_status(sport, family)
    if not st.get("proven"):
        out.update(refusal=st.get("refusal"), why=st.get("why"),
                   side=("BOOK" if st.get("refusal") in (
                       MF.R_BOOK_MARKET_RULES_NOT_CAPTURED,
                       MF.R_BOOK_SPORT_NOT_CAPTURED) else "VENUE"))
    elif key[3] is None:
        out["refusal"] = MF.R_CONTRACT_LINE
    elif not MF.half_point(key[3]):
        out["refusal"] = MF.R_NOT_HALF_POINT
    else:
        flat = MF.flat_text(rules_text)
        m = re.search(spec["statement"], flat) if flat else None
        bad = [p for p in spec["none_of"] if re.search(p, flat)]
        missing = [p for p in spec["all_of"] if not re.search(p, flat)]
        if not flat:
            out["refusal"] = MF.R_VENUE_TEXT_ABSENT
        elif bad:
            out.update(refusal=MF.R_VENUE_TEXT_CONFLICTS,
                       matched_conflicts=bad)
        elif m is None or missing:
            out.update(refusal=MF.R_VENUE_TEXT_UNRECOGNISED,
                       missing=([spec["statement"]] if m is None else [])
                       + missing)
        else:
            tl = MF._num(m.group("line"))
            want = float(key[3])
            if tl is None or (abs(tl - want) > 1e-9 if family == MF.SPREAD
                              else abs(abs(tl) - abs(want)) > 1e-9):
                out.update(refusal=MF.R_VENUE_TEXT_LINE, text_line=tl)
            else:
                clause = next((name for name, rx in _VENUE_X_CLAUSES
                               if re.search(rx, flat)), None)
                out.update(
                    established=True, period=spec["period"],
                    book=list(spec["book"]),
                    exceptional=[x["condition"]
                                 for x in spec["exceptional"]],
                    venue_exceptional_clause=clause)
    if len(_LINE_CACHE) > _LINE_CACHE_MAX:
        _LINE_CACHE.clear()
    if rules_sha256:
        _LINE_CACHE[key] = out
    return out


def _line_state(c: dict, fam: dict, rules: dict, ev: dict) -> dict:
    """The settlement state of a never-attested full-game or period line
    contract."""
    if rules.get("rules_text") is None:
        return _out(NOT_PROVEN, R_VENUE_RULES_NOT_CAPTURED, BASIS_NONE, ev)
    lt = line_terms(contract_id=c.get("contract_id"), fam=fam,
                    rules_text=rules.get("rules_text"),
                    rules_sha256=rules.get("rules_sha256"))
    ev["line_terms"] = lt
    tag = "%s/%s" % (fam.get("sport"), fam.get("family"))
    if fam.get("segment"):
        tag += "@%s" % fam["segment"]
    if not lt.get("established"):
        why = "%s:%s" % (lt.get("refusal"), tag)
        if lt.get("side") == "BOOK":
            why = "%s:%s" % (R_BOOKMAKER_TERMS_NOT_HELD, why)
        return _out(NOT_PROVEN, why, BASIS_RULES_TERMS, ev)
    conds = _short(lt.get("exceptional"))
    if not lt.get("venue_exceptional_clause"):
        return _out(NOT_PROVEN, "%s:%s:%s" % (
            R_LINE_VENUE_EXCEPTIONAL_SILENT, tag, conds), BASIS_RULES_TERMS,
            ev)
    from .. import bettor_settlement_difference_policy as SDP
    el = SDP.eligibility(sport_family=fam.get("sport"),
                         market=fam.get("family"),
                         league=c.get("competition"), settlement={},
                         lane_codes=(), precise_codes=(), match=None)
    ev["difference_policy"] = {k: el.get(k) for k in (
        "eligible", "refusal", "why")}
    return _out(NOT_PROVEN, "%s:%s:%s:%s" % (
        R_LINE_EXCEPTIONAL_DIFFER, tag,
        el.get("refusal") or "ELIGIBLE_BUT_NO_BOOK_PROBABILITY_TO_PRICE",
        conds), BASIS_RULES_TERMS, ev)


def priced_resolved(priced: dict | None) -> bool:
    """The recorded paper decision's priced settlement difference, checked
    by the capital gate's own predicate (marker, policy id, CURRENT version,
    a priced p) and the recorded eligibility verdict."""
    p = dict(priced or {})
    elig = p.get("eligibility") or {}
    pol = p.get("policy") or {}
    if elig.get("eligible") is not True or pol.get("p") is None:
        return False
    # the priced branch of bettor_capital_eligibility.settlement_resolved,
    # restated so the market plane imports no capital module (pinned equal
    # to it by tests/test_settlement_rule_registry_integration.py)
    from .. import bettor_settlement_difference_policy as SDP
    return (pol.get("policy_id") == SDP.POLICY_ID
            and pol.get("version") == SDP.VERSION
            and pol.get("p") is not None)


def _out(state, why, basis, evidence):
    return {"state": state, "why": why, "basis": basis,
            "proven": state in PROVEN_STATES,
            "evidence": dict(evidence, basis=basis, note=AUTHORITY_NOTE)}


def _book_terms_captured(fam) -> bool:
    """Does bettor_settlement_terms hold CITED money-line terms for this
    family under some context / phase (so an absent book side is a scope
    question, not a capture question)?"""
    from .. import bettor_settlement_terms as ST
    return any(k[0] == fam and k[1] == "h2h"
               for k in list(ST.BOOK_TERMS) + list(ST.PHASE_BOOK_TERMS))


def _scope_refusals() -> frozenset:
    from .. import bettor_settlement_terms as ST
    return frozenset((ST.R_CONTEXT_UNKNOWN, ST.R_SCHEDULED_ONLY,
                      ST.R_PHASE_UNKNOWN, ST.R_PHASE_EXCLUDED,
                      ST.R_FORMAT_UNKNOWN, ST.R_FORMAT_EXCLUDED))


def state_for(contract: dict, *, valuation: dict | None = None,
              rules: dict | None = None, priced: dict | None = None,
              rules_looked_up: bool = False,
              derivative_terms: bool = False) -> dict:
    """PURE (apart from the cached terms comparison). The settlement state
    of one registry contract. `valuation`: the latest decision valuation
    (settlement_verdict, refusals, decision_rules_fingerprint); `rules`: its
    market_plane_rules row (with rules_text when a terms comparison is
    needed); `priced`: {"eligibility": ..., "policy": ...} from the latest
    paper decision; `rules_looked_up`: whether the rules table was read (an
    absent row then means VENUE_RULES_NOT_CAPTURED).

    `derivative_terms` (RC6, lane D2; False is the RC5 reading exactly): a
    never-attested FULL-GAME or PERIOD LINE contract (the contract carries
    its venue `market_type`) is read against its family's captured terms
    (`_line_state`) instead of being called BOOKMAKER_TERMS_NOT_HELD, and a
    money line whose book terms ARE captured but withheld for want of the
    fixture's context / phase / format says BOOK_TERMS_SCOPE_NOT_ESTABLISHED.
    Neither ever reaches a PROVEN state."""
    c = dict(contract or {})
    ev = _rules_evidence(rules)
    v = dict(valuation or {})
    verdict = str(v.get("settlement_verdict") or "")
    refusals = [str(x) for x in (v.get("refusals") or [])]
    settle_codes = [x for x in refusals if x.startswith("SETTLEMENT")]
    ev["decision"] = ({"settlement_verdict": verdict or None,
                       "settlement_refusals": settle_codes[:6],
                       "decision_rules_fingerprint":
                           v.get("decision_rules_fingerprint")}
                      if v else None)

    # 1. CONFLICT fails closed, over anything a decision recorded
    if (rules or {}).get("parse_status") == "CONFLICT":
        return _out(CONFLICT, "%s:%s" % (R_RULE_EVIDENCE_CONFLICT,
                                         _short(sorted(ev["conflicts"]))),
                    BASIS_RULE_EVIDENCE, ev)

    cur_sha = (rules or {}).get("rules_sha256")
    dec_sha = v.get("decision_rules_fingerprint")
    changed = bool(cur_sha and dec_sha and cur_sha != dec_sha)

    # 2. A DECISION ON THE RECORD IS AUTHORITATIVE
    if v and (verdict or settle_codes):
        if verdict == "COMPATIBLE":
            if changed:
                return _out(NOT_PROVEN, R_RULES_CHANGED_SINCE_DECISION,
                            BASIS_DECISION_ATTEST, ev)
            return _out(COMPATIBLE, None, BASIS_DECISION_ATTEST, ev)
        if priced is not None and priced_resolved(priced):
            ev["priced"] = {k: (priced.get("policy") or {}).get(k) for k in (
                "policy_id", "version", "p", "q_hi")}
            if changed:
                return _out(NOT_PROVEN, R_RULES_CHANGED_SINCE_DECISION,
                            BASIS_DECISION_PRICED, ev)
            return _out(DIFFERENT_BUT_PRICED, None, BASIS_DECISION_PRICED, ev)
        why = (("SETTLEMENT_REFUSED:%s" % settle_codes[0]) if settle_codes
               else "SETTLEMENT_VERDICT:%s" % verdict)
        return _out(NOT_PROVEN, why, BASIS_DECISION_ATTEST, ev)

    # 3. NEVER ATTESTED: the captured current rules text
    if rules is None:
        return _out(NOT_PROVEN, R_VENUE_RULES_NOT_CAPTURED if rules_looked_up
                    else R_NO_COMPARISON, BASIS_NONE, ev)
    if not rules.get("rules_published"):
        return _out(EXTERNAL, R_VENUE_PUBLISHES_NO_RULES, BASIS_RULE_EVIDENCE,
                    ev)
    if str(rules.get("venue") or c.get("venue") or "") == "KALSHI" and \
            not kalshi_mapped(c):
        # (RC6) only an UNMAPPED Kalshi contract stops here; a contract the
        # Kalshi ontology mapped (kalshi_ontology: sport, family, period,
        # no gap) is judged below exactly like any other, on its own rules
        return _out(NOT_PROVEN, R_KALSHI_NOT_MAPPED, BASIS_RULE_EVIDENCE, ev)
    if derivative_terms:
        lf = line_family(c)
        if lf is not None:
            return _line_state(c, lf, rules, ev)
    fam = h2h_family(c)
    if fam is None:
        return _out(NOT_PROVEN, "%s:%s/%s/%s" % (
            R_BOOKMAKER_TERMS_NOT_HELD, c.get("sport") or "UNKNOWN_SPORT",
            c.get("family") or "UNKNOWN_FAMILY",
            c.get("period") or "UNKNOWN_PERIOD"), BASIS_RULES_TERMS, ev)
    if rules.get("rules_text") is None:
        # the row is held but its text was not loaded for this pass
        return _out(NOT_PROVEN, R_VENUE_RULES_NOT_CAPTURED, BASIS_NONE, ev)
    cmp_ = terms_comparison(sport_family=fam, league=c.get("competition"),
                            rules_text=rules.get("rules_text"),
                            rules_sha256=cur_sha)
    ev["terms"] = cmp_
    if cmp_.get("venue_self_contradictory"):
        return _out(CONFLICT, "%s:%s" % (
            R_VENUE_SELF_CONTRADICTORY,
            _short(cmp_["venue_self_contradictory"])), BASIS_RULES_TERMS, ev)
    if cmp_.get("refusal"):
        return _out(NOT_PROVEN, "%s:%s" % (R_BOOKMAKER_TERMS_NOT_HELD,
                                           cmp_["refusal"]),
                    BASIS_RULES_TERMS, ev)
    if not cmp_.get("book_terms_held"):
        refs = [str(x) for x in (cmp_.get("book_side_absent_refusals")
                                 or [])]
        if derivative_terms and refs and set(refs) <= _scope_refusals() \
                and _book_terms_captured(fam):
            return _out(NOT_PROVEN, "%s:%s" % (R_BOOK_TERMS_SCOPE,
                                               _short(refs)),
                        BASIS_RULES_TERMS, ev)
        return _out(NOT_PROVEN, "%s:%s" % (
            R_BOOKMAKER_TERMS_NOT_HELD,
            _short(cmp_.get("book_side_absent_refusals")) or "NO_CAPTURE"),
            BASIS_RULES_TERMS, ev)
    vd = cmp_.get("verdict")
    if vd == "COMPATIBLE":
        from .. import bettor_venue_settlement as V
        ties = [s for s in ev["special_conditions"]
                if s in ("TIE_SCALAR_0_50", "TIE_MANUAL_REVIEW")]
        if ties and V.BOOK_OUTCOMES.get(fam) != 3:
            return _out(NOT_PROVEN, "%s:%s" % (R_TIE_RULE_NOT_COMPARED,
                                               _short(ties)),
                        BASIS_RULES_TERMS, ev)
        return _out(COMPATIBLE, None, BASIS_RULES_TERMS, ev)
    if vd == "INCOMPATIBLE":
        from .. import bettor_settlement_difference_policy as SDP
        el = SDP.eligibility(sport_family=fam, market="h2h",
                             league=c.get("competition"),
                             settlement={"per_condition":
                                         cmp_.get("per_condition") or {}},
                             lane_codes=(), precise_codes=(), match=None)
        ev["difference_policy"] = {k: el.get(k) for k in (
            "eligible", "refusal", "refusals", "why")}
        return _out(NOT_PROVEN, "%s:%s:%s" % (
            R_INCOMPATIBLE_NOT_PRICEABLE,
            _short(cmp_.get("mismatched_conditions")),
            el.get("refusal") or "ELIGIBLE_BUT_NO_BOOK_PROBABILITY_TO_PRICE"),
            BASIS_RULES_TERMS, ev)
    per = cmp_.get("per_condition") or {}
    venue_silent = [k for k, r in per.items() if r.get("verdict") in (
        "VENUE_STATES_NO_RULE_FOR_THIS_CONDITION",
        "NEITHER_SIDE_STATES_A_RULE_FOR_THIS_CONDITION")]
    if venue_silent:
        return _out(NOT_PROVEN, "%s:%s" % (R_VENUE_RULES_SILENT_ON,
                                           _short(venue_silent)),
                    BASIS_RULES_TERMS, ev)
    book_silent = [k for k, r in per.items() if r.get("verdict") ==
                   "BOOK_STATES_NO_RULE_FOR_THIS_CONDITION"]
    return _out(NOT_PROVEN, "%s:SILENT_ON:%s" % (R_BOOKMAKER_TERMS_NOT_HELD,
                                                 _short(book_silent)),
                BASIS_RULES_TERMS, ev)
