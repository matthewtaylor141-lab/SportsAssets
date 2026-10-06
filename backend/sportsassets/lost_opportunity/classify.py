"""THE LOST OPPORTUNITY CLASSIFIER. Pure; no I/O.

ONE SETTLED REFUSE -> ONE CLASS, FROM THE DECISION-TIME RECORD ONLY.

  GOOD_REFUSAL   (a) a refusal code is a CONTROL (identity, settlement,
                 stale / unknown-freshness evidence, no qualified Pinnacle,
                 unreadable book, entries switch, no sized quantity, fees
                 not established, primary-source provenance ...) AND the
                 decision record shows the control's condition held; or
                 (b) the decision-time economics did not show an executable
                 positive net EV under the policy's own thresholds after
                 fees (gross edge >= min_gross_edge within the policy's
                 tolerance, net > 0, net >= min_net_ev_usd).
  FALSE_REFUSAL  the decision-time economics showed an executable positive
                 net EV under those thresholds, no supported control
                 applies, and the refusal came from a NAMED DEFECT:
                   CONTROL_FIRED_WITHOUT_ITS_CONDITION:<code>  the record
                       contradicts the control (e.g. STALE with age <= limit)
                   MISSING_INPUT_REFUSAL_WHILE_INPUT_PRESENT:<code>  a
                       model-input refusal while the record holds the input
                   MARKET_REFUSAL_CONTRADICTS_DECISION_TIME_FIGURES:<code>
                       an edge / net refusal while the recorded figures pass
                   <code> for a code that is a defect by itself (the gate
                       raised, the decision was not recorded)
  UNKNOWABLE     the decision-time inputs needed to price the opportunity
                 were absent (no book read, no internal probability, no net
                 EV recorded), or the record cannot verify the refusal's
                 condition either way.

THE SETTLEMENT NEVER FEEDS THE CLASS. It is used for two things only:
the caller passes only settled decisions (an unsettled market gets no row),
and `hypothetical_pnl` prices the decision-time quantity at the decision-time
executable price against the settlement payout -- labelled HYPOTHETICAL,
stored beside the class. `classify` does not read it (a test proves the
class is identical for WON and LOST settlements).

The refusal codes are copied from agents/derek_policy.py and
agents/paper_derek.py (importing those modules would pull the paper and
funded paths into this layer's import closure); a test pins the strings to
their source constants.
"""
from __future__ import annotations

from ..profitability import common as C

VERSION = "LOL_CLASSIFIER_V1"

GOOD = "GOOD_REFUSAL"
FALSE = "FALSE_REFUSAL"
UNKNOWABLE = "UNKNOWABLE"
CLASSES = (GOOD, FALSE, UNKNOWABLE)

PAPER_SOURCE = "PAPER_DECISION"
COVERAGE_SOURCE = "COVERAGE_LEDGER"
HYPOTHETICAL = "HYPOTHETICAL"

# ── the refusal codes (pinned to their sources by a test) ─────────────
R_IDENTITY = "FIXTURE_IDENTITY_NOT_ESTABLISHED"
R_NOT_REAL = "REAL_EVENT_NOT_ESTABLISHED"
R_SETTLEMENT = "SETTLEMENT_NOT_SUPPORTED"
R_NO_PINNACLE = "NO_QUALIFIED_PINNACLE_PROBABILITY"
R_STALE = "PROBABILITY_EVIDENCE_STALE"
R_FRESHNESS_UNKNOWN = "PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN"
R_NO_DEPTH = "NO_ESTABLISHED_EXECUTABLE_DEPTH"
R_LIMIT = "LIMIT_PRICE_NOT_SUPPORTED"
R_NO_QTY = "NO_SIZED_QUANTITY"
R_FEES = "FEES_NOT_ESTABLISHED"
R_CAPACITY = "NO_CAPACITY_UNDER_THE_LANE_RAILS"
R_NO_MODEL = "NO_APPROVED_INTERNAL_MODEL"
R_MODEL_CANNOT_SCORE = "INTERNAL_MODEL_CANNOT_SCORE_THIS_CANDIDATE"
R_BELOW = "BELOW_MIN_GROSS_EDGE"
R_DISAGREE = "ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE"
R_NET = "NET_EV_NOT_POSITIVE_AFTER_FEES"
R_BELOW_NET = "BELOW_MIN_NET_EV"
R_LANE = "ENTRY_LANE_REFUSED"
R_RAISED = "DEREK_GATE_RAISED"
R_NOT_RECORDED = "DEREK_DECISION_NOT_RECORDED"
# paper_derek.py
R_NO_RESEARCH_MODEL = "NO_RESEARCH_MODEL_CANDIDATE_EXISTS"
R_MODEL_UNVERIFIED = "RESEARCH_MODEL_PROVENANCE_NOT_VERIFIED"
R_RESEARCH_CANNOT_SCORE = "RESEARCH_MODEL_CANNOT_SCORE_THIS_CANDIDATE"
R_NO_BOOK = "THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY"
#: (software census closure) a read book whose bought side is valid and empty
R_SIDE_EMPTY = "THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT"
R_NOT_PMUS = "NOT_A_SUPPORTED_POLYMARKET_US_CONTRACT"
R_ORDER_REFUSED = "PAPER_RISK_REFUSED_THE_ORDER"
R_ENTRIES_DISABLED = "STRATEGY_ENTRIES_DISABLED"
R_BOOK_DEADLINE = "BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE"
# bettor_nfl_settlement.py (R30A): the NFL money line's named controls --
# each says which premise of pricing the venue contract is missing
R_NFL_TIE_RATE = "NFL_TIE_RATE_EVIDENCE_NOT_HELD"
R_NFL_TEXT = "NFL_VENUE_RULES_TEXT_NOT_RECORDED"
R_NFL_TIE_NOT_STATED = "NFL_VENUE_TIE_PAYOUT_NOT_STATED"
R_NFL_TIE_NOT_HALF = "NFL_VENUE_TIE_PAYOUT_IS_NOT_THE_CITED_HALF"
R_NFL_DRAW_LINE = "NFL_BOOK_LINE_PRICES_A_DRAW_NOT_THE_TWO_WAY_GAME_LINE"
R_NFL_EXHIBITION = "NFL_PRO_BOWL_OR_EXHIBITION_NEVER_TRADED"
R_NFL_DATE = "NFL_FIXTURE_DATE_NOT_CONSISTENT_WITH_THE_VENUE_SLUG"
R_NFL_DATE_UNREADABLE = "NFL_FIXTURE_DATE_NOT_READABLE_FROM_THE_VENUE_SLUG"
#: R30A review: the game's phase is established from the cited season
#: window; anything not established as regular season is never traded
R_NFL_PHASE = "NFL_SEASON_PHASE_NOT_ESTABLISHED_AS_REGULAR_SEASON"
NFL_SETTLEMENT_CODES = frozenset({R_NFL_TIE_RATE, R_NFL_TEXT,
                                  R_NFL_TIE_NOT_STATED, R_NFL_TIE_NOT_HALF,
                                  R_NFL_DRAW_LINE})
NFL_IDENTITY_CODES = frozenset({R_NFL_EXHIBITION, R_NFL_DATE,
                                R_NFL_DATE_UNREADABLE, R_NFL_PHASE})
# bettor_ncaaf_settlement.py (P0 incident): the college money line's named
# controls -- each names the clause or premise of the cited comparison that is
# missing (the venue text's clauses, the no-tie rule, the book capture, a
# draw-priced line), or the strict policy's payout difference, never a
# generic "unsupported". Spelled out, not imported, like the NFL codes above:
# this research package's import closure stays its own
# (tests/test_lost_opportunity_is_research_only.py), and
# tests/test_ncaaf_settlement_evidence.py pins these sets equal to the
# module's own codes.
NCAAF_SETTLEMENT_CODES = frozenset({
    "NCAAF_VENUE_RULES_TEXT_NOT_RECORDED",
    "NCAAF_VENUE_WINNER_CLAUSE_NOT_THE_CITED_COLLEGE_FOOTBALL_GAME",
    "NCAAF_VENUE_OVERTIME_CLAUSE_NOT_THE_CITED_ONE",
    "NCAAF_VENUE_TIE_CLAUSE_NOT_THE_CITED_REVIEW_CLAUSE",
    "NCAAF_VENUE_POSTPONEMENT_CLAUSE_NOT_THE_CITED_ONE",
    "NCAAF_VENUE_RESULT_SOURCE_CLAUSE_NOT_THE_CITED_ONE",
    "NCAAF_VENUE_TEXT_CARRIES_AN_UNCITED_CLAUSE",
    "NCAAF_NO_TIE_RULE_EVIDENCE_NOT_HELD",
    "NCAAF_BOOK_RULES_CAPTURE_NOT_HELD",
    "NCAAF_BOOK_LINE_PRICES_A_DRAW_NOT_THE_TWO_WAY_GAME_LINE",
    "NCAAF_STRICT_POSTPONEMENT_PAYOUTS_DIFFER",
    "NCAAF_STRICT_SUSPENSION_PAYOUTS_DIFFER",
    "NCAAF_STRICT_TIED_RESULT_VENUE_PAYOUT_NOT_STATED",
    "NCAAF_STRICT_FORFEIT_PAYOUTS_DIFFER",
    "NCAAF_STRICT_VENUE_CHANGE_VENUE_PAYOUT_NOT_STATED"})
NCAAF_IDENTITY_CODES = frozenset({
    "NCAAF_FIXTURE_DATE_NOT_CONSISTENT_WITH_THE_VENUE_SLUG",
    "NCAAF_FIXTURE_DATE_NOT_READABLE_FROM_THE_VENUE_SLUG"})

MARKET = "MARKET_NO_EDGE"
CONTROL = "CONTROL"
MISSING_INPUT = "MISSING_INPUT"
DEFECT = "DEFECT"
UNRECOGNISED = "UNRECOGNISED"

MARKET_CODES = frozenset({R_BELOW, R_DISAGREE, R_NET, R_BELOW_NET})
CONTROL_CODES = frozenset({
    R_IDENTITY, R_NOT_REAL, R_SETTLEMENT, R_NO_PINNACLE, R_STALE,
    R_FRESHNESS_UNKNOWN, R_NO_DEPTH, R_LIMIT, R_NO_QTY, R_FEES, R_CAPACITY,
    R_LANE, R_NO_BOOK, R_SIDE_EMPTY, R_NOT_PMUS, R_ORDER_REFUSED,
    R_ENTRIES_DISABLED,
    R_BOOK_DEADLINE}) | NFL_SETTLEMENT_CODES | NFL_IDENTITY_CODES | \
    NCAAF_SETTLEMENT_CODES | NCAAF_IDENTITY_CODES
MISSING_INPUT_CODES = frozenset({
    R_NO_MODEL, R_MODEL_CANNOT_SCORE, R_NO_RESEARCH_MODEL, R_MODEL_UNVERIFIED,
    R_RESEARCH_CANNOT_SCORE})
DEFECT_CODES = frozenset({R_RAISED, R_NOT_RECORDED})
DEFECT_PREFIXES = ("MODEL_RUN_RAISED",)
#: the primary-source (PinnAPI) provenance refusals are controls
CONTROL_PREFIXES = ("PINNAPI_PRIMARY_",)


def category(code) -> str:
    c = str(code or "")
    if c in MARKET_CODES:
        return MARKET
    if c in CONTROL_CODES or c.startswith(CONTROL_PREFIXES):
        return CONTROL
    if c in MISSING_INPUT_CODES:
        return MISSING_INPUT
    if c in DEFECT_CODES or c.startswith(DEFECT_PREFIXES):
        return DEFECT
    return UNRECOGNISED


# ── MANAGEMENT ATTRIBUTION (the owner's categories) ──────────────────
ATTRIBUTIONS = (
    "SETTLEMENT", "IDENTITY_MAPPING", "FRESHNESS", "LIQUIDITY",
    "EXECUTION_UNCERTAINTY", "THRESHOLD", "RISK", "CAPACITY",
    "KAREN_CHALLENGE", "UNSUPPORTED_LEAGUE", "EXPLICIT_POLICY",
    "MISSING_PROBABILITY", "MISSING_EXECUTABLE_BOOK", "DEFECT",
    "UNATTRIBUTED")
ATTRIBUTION_OF = {
    R_SETTLEMENT: "SETTLEMENT",
    R_IDENTITY: "IDENTITY_MAPPING", R_NOT_REAL: "IDENTITY_MAPPING",
    R_NOT_PMUS: "IDENTITY_MAPPING",
    R_STALE: "FRESHNESS", R_FRESHNESS_UNKNOWN: "FRESHNESS",
    R_NO_DEPTH: "LIQUIDITY", R_NO_QTY: "LIQUIDITY", R_SIDE_EMPTY: "LIQUIDITY",
    R_LIMIT: "EXECUTION_UNCERTAINTY", R_FEES: "EXECUTION_UNCERTAINTY",
    R_BELOW: "THRESHOLD", R_DISAGREE: "THRESHOLD", R_NET: "THRESHOLD",
    R_BELOW_NET: "THRESHOLD",
    R_ORDER_REFUSED: "RISK",
    R_CAPACITY: "CAPACITY",
    R_ENTRIES_DISABLED: "EXPLICIT_POLICY", R_LANE: "EXPLICIT_POLICY",
    R_NO_PINNACLE: "MISSING_PROBABILITY", R_NO_MODEL: "MISSING_PROBABILITY",
    R_MODEL_CANNOT_SCORE: "MISSING_PROBABILITY",
    R_NO_RESEARCH_MODEL: "MISSING_PROBABILITY",
    R_MODEL_UNVERIFIED: "MISSING_PROBABILITY",
    R_RESEARCH_CANNOT_SCORE: "MISSING_PROBABILITY",
    R_NO_BOOK: "MISSING_EXECUTABLE_BOOK",
    R_BOOK_DEADLINE: "MISSING_EXECUTABLE_BOOK",
    R_RAISED: "DEFECT", R_NOT_RECORDED: "DEFECT",
    **{c: "SETTLEMENT" for c in NFL_SETTLEMENT_CODES},
    R_NFL_EXHIBITION: "EXPLICIT_POLICY", R_NFL_PHASE: "EXPLICIT_POLICY",
    R_NFL_DATE: "IDENTITY_MAPPING", R_NFL_DATE_UNREADABLE: "IDENTITY_MAPPING",
    **{c: "SETTLEMENT" for c in NCAAF_SETTLEMENT_CODES},
    **{c: "IDENTITY_MAPPING" for c in NCAAF_IDENTITY_CODES}}
#: the coverage ledger's stage (ext_candidate_outcomes.stage) -> category
STAGE_ATTRIBUTION = {
    "1_PROBABILITY": "MISSING_PROBABILITY", "2_FRESHNESS": "FRESHNESS",
    "3_IDENTITY": "IDENTITY_MAPPING", "4_SETTLEMENT_SCOPE": "SETTLEMENT",
    "5_EXECUTION_ESTIMATE": "EXECUTION_UNCERTAINTY", "6_SIZING": "CAPACITY",
    "7_RISK": "RISK", "8_ECONOMICS": "THRESHOLD"}


def attribution(code, stage=None) -> str:
    """The owner's refusal category of one code (keywords for codes this
    table does not list; the coverage stage last). Never guessed beyond
    that: UNATTRIBUTED."""
    c = str(code or "")
    if c in ATTRIBUTION_OF:
        return ATTRIBUTION_OF[c]
    if c.startswith(CONTROL_PREFIXES):
        return "FRESHNESS"
    if c.startswith(DEFECT_PREFIXES):
        return "DEFECT"
    u = c.upper()
    for keys, cat in ((("KAREN",), "KAREN_CHALLENGE"),
                      (("UNSUPPORTED_LEAGUE", "LEAGUE_NOT_SUPPORTED",
                        "SPORT_NOT_SUPPORTED", "UNSUPPORTED_SPORT"),
                       "UNSUPPORTED_LEAGUE"),
                      (("SETTLEMENT",), "SETTLEMENT"),
                      (("STALE", "FRESHNESS", "BOOK_CURRENCY"), "FRESHNESS"),
                      (("IDENTITY", "MAPPING", "MAPPED", "VENUE_ABSENT",
                        "AMBIGUOUS", "NOT_LISTED"), "IDENTITY_MAPPING"),
                      (("DEPTH", "LIQUIDITY"), "LIQUIDITY"),
                      (("CAPACITY", "BUDGET"), "CAPACITY"),
                      (("RISK", "EXPOSURE", "LIMIT"), "RISK"),
                      (("PINNACLE", "PROBABILITY", "MODEL"),
                       "MISSING_PROBABILITY"),
                      (("BOOK",), "MISSING_EXECUTABLE_BOOK"),
                      (("EDGE", "NET_EV"), "THRESHOLD"),
                      (("DISABLED", "POLICY", "SWITCH"), "EXPLICIT_POLICY")):
        if any(k in u for k in keys):
            return cat
    return STAGE_ATTRIBUTION.get(str(stage or ""), "UNATTRIBUTED")


def _d(v) -> dict:
    v = C.jload(v)
    return v if isinstance(v, dict) else {}


def _blank(v) -> bool:
    return v is None or v == ""


# ═════════════════════════════════════════════════════════════════════
# DOES THE DECISION RECORD SHOW THE CONTROL'S CONDITION?  True / False /
# None (the record does not say)
# ═════════════════════════════════════════════════════════════════════

def control_supported(code, dec: dict):
    """(supported, evidence text). Reads the decision record only."""
    c = str(code or "")
    pin = _d(dec.get("pinnacle"))
    pd = _d(dec.get("policy_decision"))
    book = dec.get("book")
    book = _d(book) if book is not None else None
    val = dec.get("valuation")
    val = _d(val) if val is not None else None
    label = _d(dec.get("label"))
    if c == R_IDENTITY:
        missing = [k for k in ("us_market_slug", "fixture", "holding_side")
                   if _blank(dec.get(k))]
        if val is not None and _blank(val.get("payout_event")):
            missing.append("payout_event")
        if not missing and "pays_on" in (label.get("unknown") or {}):
            missing.append("payout_event (label.unknown.pays_on)")
        if missing:
            return True, "identity fields absent at decision: %s" % (
                ", ".join(missing))
        if val is None:
            return None, ("slug, fixture and side recorded; the payout event "
                          "is not readable (no valuation row)")
        return False, ("slug, fixture, side and payout event all recorded "
                       "at decision")
    if c == R_SETTLEMENT:
        cmp_ = _d((val or {}).get("settlement_comparison")).get(
            "compatibility")
        if cmp_ == "INCOMPATIBLE":
            return True, "valuation settlement comparison INCOMPATIBLE"
        if cmp_ == "COMPATIBLE":
            return False, "valuation settlement comparison COMPATIBLE"
        return None, "settlement compatibility not recorded (%s)" % cmp_
    if c == R_NOT_PMUS:
        if val is None or _blank(val.get("venue")):
            return None, "venue not readable"
        if val.get("venue") not in ("PMUS",):
            return True, "venue %s is not PMUS" % val.get("venue")
        return False, "venue PMUS recorded"
    if c == R_STALE:
        age, lim = C.num(pin.get("age_s")), C.num(pin.get("limit_s"))
        if age is None or lim is None:
            return None, "Pinnacle age / limit not recorded"
        if age > lim:
            return True, "Pinnacle age %.1fs > limit %.1fs" % (age, lim)
        return False, "Pinnacle age %.1fs <= limit %.1fs" % (age, lim)
    if c == R_FRESHNESS_UNKNOWN:
        age = C.num(pin.get("age_s"))
        if pin.get("at") is None or pin.get("qualification") in (
                "UNKNOWN", "CLOCKS_DISAGREE") or (age is not None
                                                   and age < 0):
            return True, "Pinnacle freshness not establishable (%s)" % (
                pin.get("qualification") or "no observed_at")
        if age is not None:
            return False, "Pinnacle age %.1fs recorded" % age
        return None, "Pinnacle freshness fields not recorded"
    if c == R_NO_PINNACLE:
        if dec.get("p_pinnacle") is None or pin.get("qualification") == \
                "ABSENT":
            return True, "no Pinnacle probability recorded"
        if pin.get("qualified") is True:
            return False, "a qualified Pinnacle probability was recorded"
        return None, "Pinnacle qualification not recorded"
    if c.startswith(CONTROL_PREFIXES):
        chk = _d(pin.get("source_check"))
        if pin.get("qualification") == "REFUSED" and chk.get("ok") is False:
            return True, "primary-source check refused: %s" % (
                chk.get("reason") or c)
        return None, "primary-source check not recorded"
    if c in (R_NO_BOOK, R_BOOK_DEADLINE, R_SIDE_EMPTY):
        if book is None:
            return True, "no book recorded at decision"
        if book.get("error") or not (book.get("levels") or []):
            return True, "book recorded with error / no levels"
        return False, "a readable book with levels was recorded"
    if c == R_ENTRIES_DISABLED:
        sw = _d(pd.get("entries_switch"))
        if sw.get("enabled") is False:
            return True, "entries switch recorded OFF (%s)" % sw.get("why")
        if sw.get("enabled") is True:
            return False, "entries switch recorded ON"
        return None, "entries switch not recorded"
    if c == R_NO_QTY:
        q = C.num(dec.get("proposed_qty"))
        if q is None or q < 1:
            return True, "no whole sized contract recorded (qty %s)" % q
        return False, "sized quantity %.4f recorded" % q
    if c == R_FEES:
        if pd and pd.get("fees_usd") is None:
            return True, "policy decision recorded no fee figure"
        if pd and pd.get("fees_usd") is not None:
            return False, "fees $%s recorded" % pd.get("fees_usd")
        return None, "no policy decision recorded"
    if c in MISSING_INPUT_CODES:
        if dec.get("p_internal") is None:
            return True, "no internal probability recorded at decision"
        return False, "internal probability %.6f recorded" % float(
            dec["p_internal"])
    return None, "the record holds no evidence for %s" % c


# ═════════════════════════════════════════════════════════════════════
# THE DECISION-TIME EXECUTABLE ECONOMICS (policy thresholds, after fees)
# ═════════════════════════════════════════════════════════════════════

def decision_economics(dec: dict) -> dict:
    """{computable, positive, net, basis, price, qty, fees, cost, edge,
    min_gross_edge, min_net_ev, why}. The policy decision's own figures
    first (net after fees and settlement states), else the economics
    headline (net after fees)."""
    pd = _d(dec.get("policy_decision"))
    econ = _d(dec.get("economics"))
    out = {"computable": False, "positive": False, "net": None,
           "basis": None, "price": None, "qty": None, "fees": None,
           "cost": None, "edge": None, "min_gross_edge": None,
           "min_net_ev": None, "why": None}
    net = C.num(pd.get("net_expected_profit_usd"))
    if pd and net is not None:
        thr = C.num(pd.get("threshold_gross_edge_fraction"))
        tol = C.num(pd.get("edge_tolerance_fraction")) or 0.0
        edge = C.num(pd.get("gross_edge_fraction"))
        mn = C.num(pd.get("min_net_ev_usd"))
        mn = 0.0 if mn is None else mn
        out.update(computable=True, net=net,
                   basis=("POLICY_DECISION_NET_AFTER_FEES_AND_SETTLEMENT_"
                          "STATES"),
                   price=C.num(pd.get("executable_price")),
                   qty=C.num(pd.get("qty")), fees=C.num(pd.get("fees_usd")),
                   cost=C.num(pd.get("acquisition_cost_usd")), edge=edge,
                   min_gross_edge=thr, min_net_ev=mn)
        out["positive"] = bool(
            net > 0 and net >= mn and edge is not None and thr is not None
            and edge >= thr - tol)
        return out
    head = econ.get("headline") if econ.get("ok") else None
    head = head if isinstance(head, dict) else {}
    hnet = C.num(head.get("expected_net_profit_usd"))
    if hnet is not None:
        prm = _d(econ.get("params"))
        mn = C.num(prm.get("min_net_ev_usd"))
        mn = 0.0 if mn is None else mn
        out.update(computable=True, net=hnet,
                   basis="ECONOMICS_HEADLINE_NET_AFTER_FEES",
                   price=C.num(econ.get("executable_price")),
                   qty=C.num(econ.get("qty")),
                   fees=C.num(econ.get("fees_usd")),
                   cost=C.num(econ.get("acquisition_cost_usd")),
                   edge=C.num(head.get("gross_edge_pp")),
                   min_gross_edge=C.num(prm.get("min_gross_edge_pp")),
                   min_net_ev=mn)
        out["positive"] = bool(hnet > 0 and hnet >= mn
                               and head.get("clears_min_gross_edge") is True)
        return out
    if dec.get("source") == COVERAGE_SOURCE:
        out["why"] = ("COVERAGE_LEDGER_REFUSAL_BEFORE_ANY_PRICE: the coverage "
                      "ledger records the refusal code only (no side, no "
                      "book, no probability)")
    elif dec.get("book") is None:
        out["why"] = ("NO_DECISION_TIME_BOOK_RECORDED: the refusal fired "
                      "before the book was read")
    elif dec.get("p_internal") is None:
        out["why"] = ("NO_INTERNAL_PROBABILITY_AT_DECISION: the policy needs "
                      "both qualified estimates; there is no Pinnacle-only "
                      "fallback")
    elif dec.get("p_blended") is None:
        out["why"] = "NO_BLENDED_PROBABILITY_AT_DECISION"
    else:
        out["why"] = "NO_NET_EV_RECORDED_AT_DECISION"
    return out


# ═════════════════════════════════════════════════════════════════════
# THE CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════

def refusal_codes(dec: dict) -> list:
    codes = [str(x) for x in (dec.get("refusals") or []) if x]
    if not codes and dec.get("refusal"):
        codes = [str(dec["refusal"])]
    return codes


def classify(dec: dict) -> dict:
    """The class of one REFUSE from its decision-time record, with the
    management attribution of the refusal that decided it. Never reads a
    settlement."""
    got = _classify(dec)
    codes = refusal_codes(dec)
    code = got.pop("attribution_code", None)
    if code is None:
        code = codes[0] if codes else None
    got["attribution_code"] = code
    got["attribution"] = ("THRESHOLD" if code == "NO_EXECUTABLE_POSITIVE_"
                          "NET_EV" else attribution(code, dec.get("stage")))
    return got


def _classify(dec: dict) -> dict:
    codes = refusal_codes(dec)
    econ = decision_economics(dec)
    checks = {}
    for c in codes:
        cat = category(c)
        if cat in (CONTROL, MISSING_INPUT):
            ok, ev = control_supported(c, dec)
            checks[c] = {"category": cat, "supported": ok, "evidence": ev}
        else:
            checks[c] = {"category": cat, "supported": None,
                         "evidence": None}
    cats = sorted({v["category"] for v in checks.values()})
    first_cat = checks[codes[0]]["category"] if codes else UNRECOGNISED
    base = {"defect": None, "defects": [], "checks": checks,
            "economics": econ, "refusal_category": first_cat,
            "categories": cats}

    if dec.get("source") == COVERAGE_SOURCE:
        return dict(base, classification=UNKNOWABLE, reason=(
            "%s; refusal %s cannot be verified or priced from the coverage "
            "ledger" % (econ["why"], ", ".join(codes) or "(none)")))

    controls = [c for c in codes if checks[c]["category"] == CONTROL
                and checks[c]["supported"] is True]
    if controls:
        c = controls[0]
        return dict(base, classification=GOOD, attribution_code=c, reason=(
            "CORRECT_CONTROL %s: %s" % (c, checks[c]["evidence"])))

    if not econ["computable"]:
        blocking = [c for c in codes if checks[c]["category"] ==
                    MISSING_INPUT]
        return dict(base, classification=UNKNOWABLE,
                    attribution_code=(blocking[0] if blocking else None),
                    reason=(
            "MISSING_DECISION_TIME_INPUT %s%s" % (
                econ["why"], "" if not blocking else
                "; blocking input refusal: %s" % ", ".join(blocking))))

    if not econ["positive"]:
        market = [c for c in codes if checks[c]["category"] == MARKET]
        return dict(base, classification=GOOD,
                    attribution_code=(market[0] if market else
                                      "NO_EXECUTABLE_POSITIVE_NET_EV"),
                    reason=(
            "NO_EXECUTABLE_POSITIVE_NET_EV_AT_DECISION: net $%.4f (%s), "
            "gross edge %s vs min %s, min net $%.2f -- the decision-time "
            "evidence did not support entry; the settlement outcome is not "
            "evidence" % (econ["net"], econ["basis"], _fmt(econ["edge"]),
                          _fmt(econ["min_gross_edge"]),
                          econ["min_net_ev"] or 0.0)))

    defects = []
    non_market = [c for c in codes if checks[c]["category"] != MARKET]
    for c in non_market:
        chk = checks[c]
        if chk["category"] == DEFECT:
            defects.append(c)
        elif chk["category"] == CONTROL and chk["supported"] is False:
            defects.append("CONTROL_FIRED_WITHOUT_ITS_CONDITION:%s" % c)
        elif chk["category"] == MISSING_INPUT and chk["supported"] is False:
            defects.append("MISSING_INPUT_REFUSAL_WHILE_INPUT_PRESENT:%s" % c)
    if not non_market:
        defects.append("MARKET_REFUSAL_CONTRADICTS_DECISION_TIME_FIGURES:%s"
                       % ",".join(codes or ["(no refusal code)"]))
    if defects:
        return dict(base, classification=FALSE, defect=defects[0],
                    defects=defects, attribution_code=(
                        defects[0].split(":", 1)[-1]), reason=(
                        "EXECUTABLE_POSITIVE_NET_EV_REFUSED_BY_DEFECT %s: net "
                        "$%.4f (%s) cleared the policy's own thresholds after "
                        "fees, and no supported control applied" % (
                            defects[0], econ["net"], econ["basis"])))
    unverifiable = [c for c in non_market if checks[c]["supported"] is None]
    return dict(base, classification=UNKNOWABLE, reason=(
        "REFUSAL_CONDITION_NOT_VERIFIABLE_FROM_THE_RECORD %s: net $%.4f "
        "positive at decision, but the record neither shows nor contradicts "
        "the refusal's condition" % (", ".join(unverifiable or non_market),
                                     econ["net"])))


def _fmt(v):
    return "n/a" if v is None else "%.4f" % v


# ═════════════════════════════════════════════════════════════════════
# THE HYPOTHETICAL P&L (stored beside the class; never feeds it)
# ═════════════════════════════════════════════════════════════════════

def side_payout(dec: dict, st: dict):
    """(payout per contract for the decision's holding side, basis) from a
    settlement of the same market slug: the same side's own row, else the
    binary complement of the opposite side's row."""
    side = dec.get("holding_side")
    if not st or side is None:
        return None, "NO_HOLDING_SIDE_RECORDED"
    ppc = C.num(st.get("payout_per_contract"))
    if st.get("holding_side") == side:
        return ppc, "SAME_CONTRACT_SAME_SIDE_SETTLEMENT"
    if st.get("outcome") in ("WON", "LOST") and ppc is not None:
        return 1.0 - ppc, "SAME_CONTRACT_OPPOSITE_SIDE_BINARY_COMPLEMENT"
    if st.get("outcome") == "VOID_REFUND":
        return None, "OPPOSITE_SIDE_VOID_REFUND_PRICE_FOR_THIS_SIDE_UNKNOWN"
    return None, "SETTLEMENT_PAYOUT_NOT_READABLE"


def hypothetical_pnl(dec: dict, econ: dict, st: dict) -> dict:
    """payout x qty - acquisition cost - fees, at the DECISION-TIME
    executable price and quantity. Labelled HYPOTHETICAL."""
    ppc, basis = side_payout(dec, st)
    out = {"label": HYPOTHETICAL, "value": None, "why": None,
           "payout_per_contract": ppc, "payout_basis": basis}
    if dec.get("source") == COVERAGE_SOURCE:
        out["why"] = "NO_DECISION_TIME_EXECUTABLE_PRICE_OR_SIDE_RECORDED"
        return out
    if ppc is None:
        out["why"] = basis
        return out
    qty, cost, fees = econ.get("qty"), econ.get("cost"), econ.get("fees")
    if qty is None or cost is None:
        out["why"] = ("NO_DECISION_TIME_EXECUTABLE_PRICE: %s" % (
            econ.get("why") or "no priced quantity recorded"))
        return out
    if fees is None:
        out["why"] = "FEES_NOT_ESTABLISHED_AT_DECISION"
        return out
    out["value"] = round(qty * ppc - cost - abs(fees), 6)
    return out
