"""DEREK'S ENTRY POLICY -- THE OWNER'S ENTRY INSTRUCTION, WITH ITS UNITS.

    ACTIVE: DEREK_ENTRY_POLICY_V2 (combination BLENDED_AVERAGE_MIN_GROSS_EDGE).
    RETAINED FOR REPLAY AND COMPARISON: DEREK_ENTRY_POLICY_V1 (combination
    CONSERVATIVE_AGREEMENT). Both run through the one pure function
    `decide_entry`; every decision records which one judged it.

    "5%+ EV" is read, by default, as a FIVE-PERCENTAGE-POINT GROSS PROBABILITY
    EDGE on a $0/$1 contract:

        policy_probability - executable_acquisition_price >= min_gross_edge_pp

    with `min_gross_edge_pp = 0.05` in PROBABILITY POINTS (0.05 = 5 pp). It is
    NOT a 5% return on capital: a 5 pp edge bought at $0.50 is a 10% gross return
    on the $0.50, and the same 5 pp bought at $0.90 is 5.6%. Both are displayed,
    separately, and only the probability-point edge is the threshold.

── V2 (ACTIVE): THE BLENDED AVERAGE ────────────────────────────────────────

    The owner's instruction, verbatim in arithmetic:

        blended_probability = (internal_probability + pinnacle_fair_probability) / 2
        gross_edge          = blended_probability - executable acquisition price
                              (per $1 contract, depth-weighted over the size)
        ENTER only if gross_edge >= 0.05 AND expected net profit after fees and
        execution costs > 0.

    BOTH INPUTS MUST BE PRESENT AND QUALIFIED: the approved internal model
    (bettor_funded_model.KEY_ENTRY_PAYOUT, state APPROVED, provenance verified,
    approved before the decision, able to score it) and the de-vigged Pinnacle
    probability (PINNACLE_DEVIG_V1) that passes the lane's own freshness rule.
    Either missing or unqualified refuses BY NAME (NO_APPROVED_INTERNAL_MODEL,
    INTERNAL_MODEL_CANNOT_SCORE_THIS_CANDIDATE, NO_QUALIFIED_PINNACLE_PROBABILITY,
    PROBABILITY_EVIDENCE_STALE, PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN): nothing
    is averaged with a missing value and there is no Pinnacle-only fallback.

    WHAT THE AVERAGE IS NOT. The internal model is a calibration fitted to
    market prices (its inputs are the venue price and the payout side), not an
    independent sports forecast. The blended probability is therefore an
    average of two market-derived estimates; it is not independent
    confirmation of an edge, and it is displayed as such.

── V1 (RETAINED): CONSERVATIVE AGREEMENT ──────────────────────────────────

    The de-vigged Pinnacle probability AND the approved internal model's
    probability must EACH clear the threshold at the same executable price.
    Nothing is averaged; the headline economics are the LOWER of the two.
    Replayed on recorded inputs by `derek_policy_replay`; never binding.

── NET OF FEES ─────────────────────────────────────────────────────────────

    Expected net profit = sum over the depth walk of qty_i * (p - price_i)
    minus the deployed fee schedule charged per fill,
    `bettor_funded_book.fee_for(qty_i, price_i)` -- the function the funded book
    books fees with -- and it must be POSITIVE. `p` is the policy's own
    probability (V2: the blended average). The acquisition cost is the
    executable price PLUS the fee; the valuation row's `cost_per_contract` is
    that FEE, not the cost. Expected return on capital = net / (acquisition
    cost + fees). A further net threshold is a separately named parameter,
    `min_net_ev_usd` (dollars, default 0), never folded into the gross edge.

── ONE FUNCTION FOR EXECUTION AND FOR DISPLAY ──────────────────────────────

    `decide_entry(policy, internal=, pinnacle=, econ=)` is the combination
    rule. `evaluate` takes the combination check and C_NET_EV from it, the
    decision record stores its whole output (evidence.policy_decision: policy
    name and version, internal p with its model version and time, Pinnacle p
    with its time, blended p, gross edge, fees, net expected profit, expected
    return on capital, each condition's pass/fail) and copies the headline
    columns from it, and Derek's workspace reads that stored output.

── THE FLOAT BOUNDARY, DELIBERATELY ────────────────────────────────────────

    The edge is the DECIMAL difference of the two recorded numbers (so 0.60 -
    0.55 is exactly 0.05, not 0.04999999999999993), and it qualifies when it is
    >= min_gross_edge_pp - EDGE_TOLERANCE_PP with EDGE_TOLERANCE_PP = 1e-9. A
    probability or price carries no meaning below 1e-9, so an edge within 1e-9
    below 0.05 is binary representation error and is treated as exactly 0.05.
    Exactly 0.05 QUALIFIES. 0.0499 does not.

── WHAT DEREK ESTABLISHES BEFORE A PURCHASE ────────────────────────────────

    Eleven named checks, each PASS / FAIL / UNKNOWN, each read from the gate
    that already owns it (never re-implemented here): identity (fixture,
    participants, date, side, period), a real (not simulated/demonstration)
    event, applicable settlement rules, qualified current probability evidence
    (the lane's own freshness rule, `_entry_freshness`), established book
    currency and executable depth, the exact supported tick and limit
    (`bettor_funded_execution.plan_from_decision`), fees (`fee_for`), account
    and reconciliation eligibility (`bettor_funded_activation.account_selection`),
    capital and inventory capacity (the lane's rails), limits / reservations /
    authorization (owner-approved limits and the authorization record), and the
    enabled submission state (the code switches).

    POLICY checks decide Derek's verdict; UNKNOWN blocks. The EXECUTION-AUTHORITY
    checks (account, owner limits/authorization, submission switch) are recorded
    with their dependency class and are ENFORCED BY THE FUNDED CONNECTOR, the
    one execution authority -- so Derek may say ENTER while the connector still
    refuses to send, and the record shows exactly which owner decision stands in
    the way. Derek sends nothing.

── `rec`, THE DICT `_funded_attempt` RECEIVES ──────────────────────────────

    `gate_for_funded_entry` reads these keys of the entry lane's record
    (`bettor_external_shadow.evaluate` output, extended in
    `ext_pinnacle_loop.cycle` before `_funded_attempt`):

      record_purpose, experiment_id, version, devig_method, valuation.overround,
      probability, observed_at, received_at, payout_event, payout_is_complement,
      executable_price, executable_price_basis, cost_per_contract, admissible,
      refusals, proposed_size,
      contract.{venue, condition_id, us_market_slug, buy_intent, selection,
                period, period_basis, sport_family, market, event_key},
      execution_plan.{execution, risk, research_waiver},
      venue_quote.{book_currency, read_at, venue_clock, http_observation,
                   subscription, revalidation, venue_ts, age_s},
      settlement.{overall_established, unmet, fixture_metadata},
      settlement_comparison.{compatibility, fixture_read, fixture_game_pk},
      payout_binding.ok, valuation_row_id, us_market_slug, event_key,
      order_intent, buy_intent.

    A missing key reads as not established; it never defaults to a pass.
"""

from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

AGENT_ID = "DEREK"
POLICY_KEY = "DEREK_ENTRY_POLICY"
#: ── THE POLICY VERSIONS. V1 is retained for replay and comparison only. ──
POLICY_V1 = "DEREK_ENTRY_POLICY_V1"
POLICY_V2 = "DEREK_ENTRY_POLICY_V2"
COMBINATION_V1 = "CONSERVATIVE_AGREEMENT"
COMBINATION_V2 = "BLENDED_AVERAGE_MIN_GROSS_EDGE"
POLICY_RULES = {POLICY_V1: COMBINATION_V1, POLICY_V2: COMBINATION_V2}
#: THE ACTIVE POLICY: the one the funded gate and the scheduled pass bind.
ACTIVE_POLICY = POLICY_V2
POLICY_VERSION = ACTIVE_POLICY
COMBINATION_POLICY = POLICY_RULES[ACTIVE_POLICY]

#: THE ENTRY THRESHOLD'S VERSIONED DEFAULT, as a PROBABILITY DIFFERENCE on
#: a $0/$1 contract: 0.05 means 5 percentage points (qualified probability
#: minus executable price >= 0.05). It is never written as 5, and it is not
#: a return. (The policy key keeps its established name, min_gross_edge_pp,
#: whose value is this same probability difference.) A module-level
#: constant so an improvement candidate is a one-line, reviewable diff
#: (tools/improvement_sandbox.py edits exactly this assignment).
MIN_GROSS_EDGE_PROBABILITY = 0.05  # versioned default

#: THE CODE DEFAULT. A registry-held ACTIVE version (agents.registry) may
#: supply other values; the version label then travels with every decision.
DEFAULT_PARAMS = {
    #: probability points on a $0/$1 contract: 0.05 = 5 pp. NOT a return.
    "min_gross_edge_pp": MIN_GROSS_EDGE_PROBABILITY,
    #: dollars of expected net profit after fees, for the whole quantity.
    #: The policy requires > 0 whatever this says; this adds a further bar.
    "min_net_ev_usd": 0.0,
}
PARAM_UNITS = {
    "min_gross_edge_pp": ("PROBABILITY_DIFFERENCE_ON_A_0_TO_1_DOLLAR_CONTRACT "
                          "(0.05 == 5 percentage points; never 5)"),
    "min_net_ev_usd": "US_DOLLARS_FOR_THE_WHOLE_QUANTITY",
}
EDGE_TOLERANCE_PP = 1e-9

ENTER = "ENTER"
REFUSE = "REFUSE"

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
BLOCKS_POLICY = "POLICY"
BLOCKS_EXECUTION = "EXECUTION_AUTHORITY"

#: ── WHO OR WHAT CAN CLEAR A BLOCKER (the owner's classification) ─────────
DEP_ENGINEERING_CONFIGURATION = "ENGINEERING_CONFIGURATION"
DEP_EVIDENCE = "EVIDENCE"
DEP_ENGINEERING = "ENGINEERING"
DEP_ELAPSED_TIME = "ELAPSED_TIME"
DEP_OWNER_DECISION = "OWNER_DECISION"
#: Not a dependency at all: the market was measured and offered no
#: qualifying edge. Reported so it is never mistaken for a blocker.
DEP_NONE_MARKET = "NONE__THE_MARKET_OFFERED_NO_QUALIFYING_EDGE"
DEPENDENCY_CLASSES = (DEP_ENGINEERING_CONFIGURATION, DEP_EVIDENCE,
                      DEP_ENGINEERING, DEP_ELAPSED_TIME, DEP_OWNER_DECISION)

#: ── THE NAMED CHECKS ─────────────────────────────────────────────────────
C_PURPOSE = "entry_decision_record"
C_IDENTITY = "fixture_participants_date_side_period"
C_REAL = "real_event_not_simulated"
C_SETTLEMENT = "applicable_settlement_rules"
C_PROBABILITY = "qualified_current_probability_evidence"
C_DEPTH = "book_currency_and_executable_depth"
C_TICK = "supported_tick_and_limit_price"
C_FEES = "applicable_fees"
C_ACCOUNT = "account_and_reconciliation_eligibility"
C_CAPACITY = "available_capital_and_inventory_capacity"
C_LIMITS = "limits_reservations_and_authorization"
C_SUBMISSION = "enabled_submission_state"
C_MODEL = "approved_internal_model"
C_AGREEMENT = "conservative_agreement_min_gross_edge"      # V1 only
C_BLENDED = "blended_average_min_gross_edge"              # V2
COMBINATION_CHECK = {POLICY_V1: C_AGREEMENT, POLICY_V2: C_BLENDED}
C_NET_EV = "positive_net_ev_after_fees"
C_LANE = "entry_lane_admission"
PRE_PURCHASE_CHECKS = (C_IDENTITY, C_REAL, C_SETTLEMENT, C_PROBABILITY,
                       C_DEPTH, C_TICK, C_FEES, C_ACCOUNT, C_CAPACITY,
                       C_LIMITS, C_SUBMISSION)

#: ── REFUSALS ─────────────────────────────────────────────────────────────
R_NOT_ENTRY = "NOT_AN_ENTRY_DECISION_RECORD"
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
R_DISAGREE = "ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE"   # V1 only
R_NET = "NET_EV_NOT_POSITIVE_AFTER_FEES"
R_BELOW_NET = "BELOW_MIN_NET_EV"
R_LANE = "ENTRY_LANE_REFUSED"
R_RAISED = "DEREK_GATE_RAISED"
R_NOT_RECORDED = "DEREK_DECISION_NOT_RECORDED"

DECIDED_BY_GATE = "FUNDED_ENTRY_GATE"
DECIDED_BY_CYCLE = "AFTER_CYCLE"

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"

#: The lane's refusal stages (bettor_external_shadow.STAGES) mapped to the
#: check that owns them, so a lane refusal is reported under its check.
STAGE_TO_CHECK = {
    "1_PROBABILITY": C_PROBABILITY,
    "2_FRESHNESS": C_PROBABILITY,
    "3_IDENTITY": C_IDENTITY,
    "4_SETTLEMENT_SCOPE": C_SETTLEMENT,
    "5_EXECUTION_ESTIMATE": C_DEPTH,
    "6_SIZING": C_CAPACITY,
    "7_RISK": C_CAPACITY,
}
#: Venue-currency codes the lane files under freshness belong to depth here.
VENUE_CURRENCY_CODES = ("VENUE_BOOK_CURRENCY_NOT_ESTABLISHED",
                        "VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT",
                        "VENUE_BOOK_STALE")


# ═════════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════════

def _j(v):
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from .. import bettor_pinnacle_devig as devig
        return float(devig._epoch(v))
    except Exception:                                          # noqa: BLE001
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _dec(v) -> Decimal | None:
    try:
        return None if v is None else Decimal(repr(float(v)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def gross_edge(p, price) -> float | None:
    """p - price in probability points, as the DECIMAL difference of the two
    recorded numbers (see the module docstring on the float boundary)."""
    a, b = _dec(p), _dec(price)
    if a is None or b is None:
        return None
    return float(a - b)


def clears(edge, threshold) -> bool:
    """edge >= threshold, with EDGE_TOLERANCE_PP for representation error.
    Exactly the threshold qualifies."""
    if edge is None or threshold is None:
        return False
    return float(edge) >= float(threshold) - EDGE_TOLERANCE_PP


def _check(name, status, detail, *, blocks=BLOCKS_POLICY, refusal=None,
           dependency=None, source=None, evidence=None) -> dict:
    return {"check": name, "status": status, "detail": detail,
            "blocks": blocks,
            "refusal": (None if status == PASS else refusal),
            "dependency": (None if status == PASS else dependency),
            "source": source, "evidence": evidence or {}}


def fixture_of(*, condition_id=None, event_key=None, us_market_slug=None):
    """The fixture a decision is counted under. Repeated decisions on one
    fixture are ONE independent example; this key is what makes that countable."""
    if condition_id:
        return "condition:%s" % condition_id
    if event_key:
        return "event:%s" % event_key
    if us_market_slug:
        return "slug:%s" % us_market_slug
    return None


def decision_id_for(*, valuation_id=None, rec_digest=None,
                    policy_version=POLICY_VERSION) -> str:
    """Deterministic: the funded gate and the after-cycle pass derive the same
    id for one valuation, so a decision is recorded once."""
    if valuation_id is not None:
        return "derek:%s:val:%s" % (policy_version, valuation_id)
    return "derek:%s:rec:%s" % (policy_version, rec_digest or "unknown")


def entry_features(cand: dict) -> dict | None:
    """The internal entry model's decision-time vector, or None when the
    candidate carries no executable price to score at."""
    price = cand.get("executable_price")
    if price is None:
        return None
    return {"acquisition_price": round(float(price), 9),
            "payout_is_complement": 1.0 if cand.get("payout_is_complement")
            else 0.0}


# ═════════════════════════════════════════════════════════════════════════
# THE CANDIDATE, NORMALISED FROM EITHER SOURCE
# ═════════════════════════════════════════════════════════════════════════

def _lane_stage(code) -> str | None:
    from .. import bettor_external_shadow as ext
    return ext.STAGE_OF.get(str(code).split(":")[0])


def _freshness_view(fr: dict | None, *, basis: str) -> dict:
    fr = dict(fr or {})
    p_age, p_lim = _f(fr.get("pinnacle_age_s")), _f(fr.get("pinnacle_limit_s"))
    v_age, v_lim = _f(fr.get("venue_age_s")), _f(fr.get("venue_limit_s"))
    d, d_lim = (_f(fr.get("our_processing_delay_s")),
                _f(fr.get("our_processing_delay_limit_s")))
    if p_lim is None:
        from ..workers import ext_pinnacle_loop as L
        p_lim = L.PINNACLE_MAX_AGE_S

    def _q(age, lim):
        if age is None or lim is None or age < 0:
            return "UNKNOWN"              # a future stamp is not freshness
        return "FRESH" if age <= lim else "STALE"
    venue_q = _q(v_age, v_lim)
    if venue_q == "FRESH" and d is not None and d_lim is not None \
            and d > d_lim:
        venue_q = "STALE"
    return {"fresh": fr.get("fresh"), "why": fr.get("why"),
            "pinnacle_age_s": p_age, "pinnacle_limit_s": p_lim,
            "pinnacle_qualification": _q(p_age, p_lim),
            "venue_age_s": v_age, "venue_limit_s": v_lim,
            "venue_qualification": venue_q,
            "our_processing_delay_s": d,
            "venue_age_basis": fr.get("venue_age_basis"),
            "unknown_side": fr.get("unknown_side"),
            "rule": ("ext_pinnacle_loop._entry_freshness: both clocks re-aged "
                     "at the decision, the stalest governs; UNKNOWN blocks"),
            "basis": basis}


def candidate_from_rec(rec: dict, *, now: float) -> dict:
    """The live record `_funded_attempt` holds, normalised. Freshness is the
    lane's own rule RE-EVALUATED AT `now` when the venue quote is on the
    record, otherwise the verdict the lane recorded at its decision instant
    (said so in `freshness.basis`)."""
    rec = dict(rec or {})
    c = dict(rec.get("contract") or {})
    plan = dict(rec.get("execution_plan") or {})
    est = dict(plan.get("execution") or {})
    risk = dict(plan.get("risk") or {})
    vq = rec.get("venue_quote")
    fr, basis = None, None
    if isinstance(vq, dict) and rec.get("observed_at") is not None:
        try:
            from ..workers import ext_pinnacle_loop as L
            fr = L._entry_freshness({"observed_at": rec.get("observed_at"),
                                     "received_at": rec.get("received_at")},
                                    vq, float(now))
            basis = "RE_EVALUATED_AT_THE_GATE_INSTANT"
        except Exception as exc:                               # noqa: BLE001
            fr, basis = None, "RE_EVALUATION_RAISED:%s" % type(exc).__name__
    if fr is None:
        fr = risk.get("freshness_evidence") or {}
        basis = basis or "AS_RECORDED_AT_THE_LANE_DECISION_INSTANT"
    srule = dict(rec.get("settlement") or {})
    scmp = dict(rec.get("settlement_comparison") or {})
    fmeta = dict(srule.get("fixture_metadata") or {})
    cur = dict((vq or {}).get("book_currency") or {}) if isinstance(vq, dict) \
        else {}
    slug = rec.get("us_market_slug") or c.get("us_market_slug")
    side = rec.get("order_intent") or rec.get("buy_intent") or \
        c.get("buy_intent")
    val = dict(rec.get("valuation") or {})
    return {
        "source": "REC",
        "valuation_id": rec.get("valuation_row_id"),
        "record_purpose": rec.get("record_purpose"),
        "experiment_id": rec.get("experiment_id"),
        "venue": c.get("venue"),
        "condition_id": c.get("condition_id"),
        "us_market_slug": slug,
        "event_key": rec.get("event_key") or c.get("event_key"),
        "fixture": fixture_of(condition_id=c.get("condition_id"),
                              event_key=rec.get("event_key")
                              or c.get("event_key"),
                              us_market_slug=slug),
        "side": side,
        "payout_event": rec.get("payout_event") or c.get("payout_event"),
        "payout_is_complement": bool(rec.get("payout_is_complement")),
        "selection": c.get("selection"),
        "sport_family": c.get("sport_family"),
        "market": c.get("market"),
        "line": _f(c.get("line")),
        "period": c.get("period"),
        "period_basis": c.get("period_basis"),
        "pinnacle": {"p": _f(rec.get("probability")),
                     "observed_at": _epoch(rec.get("observed_at")),
                     "received_at": _epoch(rec.get("received_at")),
                     "overround": _f(val.get("overround")),
                     "method": rec.get("devig_method"),
                     "source_version": rec.get("version")},
        "executable_price": _f(rec.get("executable_price")),
        "executable_price_basis": rec.get("executable_price_basis"),
        "lane_fee_per_contract": _f(rec.get("cost_per_contract")),
        "est": est, "risk": risk,
        "freshness": _freshness_view(fr, basis=basis),
        # THE DECISION-INSTANT CURRENCY VERDICT when the freshness rule was
        # re-evaluated here (it re-runs bettor_venue_currency.evaluate on the
        # read's own evidence); otherwise the verdict carried from the read.
        "book_currency": {
            "verdict": (fr or {}).get("venue_currency_verdict")
            or cur.get("verdict"),
            "mechanism": ((fr or {}).get("venue_age_basis")
                          if (fr or {}).get("venue_currency_verdict")
                          else cur.get("mechanism")),
            "basis": ("RE_EVALUATED_AT_THE_DECISION_INSTANT"
                      if (fr or {}).get("venue_currency_verdict")
                      else "AS_READ")},
        "venue_read_at": _f((vq or {}).get("read_at"))
        if isinstance(vq, dict) else None,
        "settlement": {"compatibility": scmp.get("compatibility"),
                       "overall_established": (
                           srule.get("overall_established") is True
                           and scmp.get("compatibility") == "COMPATIBLE"),
                       "unmet": srule.get("unmet"),
                       "blockers": list(scmp.get("blockers") or []),
                       "fixture_read": (scmp.get("fixture_read")
                                        if "fixture_read" in scmp
                                        else fmeta.get("read")),
                       "game_pk": scmp.get("fixture_game_pk")
                       or fmeta.get("game_pk"),
                       "official_date": fmeta.get("official_date"),
                       "home_team": fmeta.get("home_team"),
                       "away_team": fmeta.get("away_team")},
        "payout_binding_ok": (None if rec.get("payout_binding") is None
                              else bool((rec.get("payout_binding") or {})
                                        .get("ok"))),
        "admissible": bool(rec.get("admissible")),
        "refusals": [str(x) for x in (rec.get("refusals") or [])],
        "plan_input": rec,
        "decided_at": float(now),
    }


def candidate_from_row(row: dict) -> dict:
    """An `external_valuations` row, normalised AS OF ITS OWN DECISION INSTANT:
    freshness is the verdict the lane's rule reached then (recorded on the
    row's risk verdict), never re-aged to a later clock."""
    r = dict(row or {})
    est = _j(r.get("execution_estimate")) or {}
    risk = _j(r.get("risk_verdict")) or {}
    scmp = _j(r.get("settlement_comparison")) or {}
    fr = dict(risk.get("freshness_evidence") or {})
    slug = r.get("us_market_slug")
    decided = _epoch(r.get("decided_at"))
    refusals = [str(x) for x in (r.get("refusals") or [])]
    # The row carries no `overall_established`; its settlement-stage refusals
    # say whether the lane established the rules.
    settle_unmet = [c for c in refusals
                    if _lane_stage(c) == "4_SETTLEMENT_SCOPE"
                    or c == "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"]
    plan_input = {"record_purpose": r.get("record_purpose"),
                  "admissible": bool(r.get("admissible")),
                  "refusals": refusals,
                  "execution_plan": {"execution": est, "risk": risk},
                  "us_market_slug": slug,
                  "event_key": r.get("event_key"),
                  "order_intent": r.get("buy_intent"),
                  "payout_event": r.get("payout_event")}
    return {
        "source": "ROW",
        "valuation_id": r.get("id"),
        "record_purpose": r.get("record_purpose"),
        "experiment_id": r.get("experiment_id"),
        "venue": r.get("venue"),
        "condition_id": r.get("condition_id"),
        "us_market_slug": slug,
        "event_key": r.get("event_key"),
        "fixture": fixture_of(condition_id=r.get("condition_id"),
                              event_key=r.get("event_key"),
                              us_market_slug=slug),
        "side": r.get("buy_intent"),
        "payout_event": r.get("payout_event"),
        "payout_is_complement": bool(r.get("payout_is_complement")),
        "selection": r.get("contract_selection"),
        "sport_family": r.get("sport_family"),
        "market": r.get("market"),
        "line": _f(r.get("line")),
        "period": r.get("period"),
        "period_basis": ("RECORDED_ON_THE_VALUATION_ROW"
                         if r.get("period") else None),
        "pinnacle": {"p": _f(r.get("probability")),
                     "provider": r.get("provider"),
                     "reference_input": scmp.get("reference_input"),
                     "observed_at": _epoch(r.get("observed_at")),
                     "received_at": _epoch(r.get("received_at")),
                     "overround": _f(r.get("overround")),
                     "method": r.get("devig_method"),
                     "source_version": r.get("version")},
        "executable_price": _f(r.get("executable_price")),
        "executable_price_basis": est.get("acquisition_cost_is"),
        "lane_fee_per_contract": _f(r.get("cost_per_contract")),
        "est": est, "risk": risk,
        "freshness": _freshness_view(
            fr, basis="AS_RECORDED_AT_THE_LANE_DECISION_INSTANT"),
        # AN ENTRY_DECISION ROW EXISTS ONLY WHEN THE VENUE READ ESTABLISHED
        # CURRENCY (otherwise it is a calibration-only row, migration 144).
        "book_currency": {"verdict": (
            "BOOK_CURRENCY_ESTABLISHED"
            if r.get("record_purpose") == "ENTRY_DECISION" else None),
            "mechanism": fr.get("venue_age_basis")},
        "venue_read_at": None,
        # ESTABLISHED ONLY ON POSITIVE EVIDENCE. The absence of a recognised
        # settlement-stage refusal on the row is NOT evidence that the rules
        # were established -- valuation 2059 carried compatibility=UNKNOWN
        # and read here as overall_established=True. Now: the row's own
        # recorded establishment (written since the blocker projection) AND
        # a COMPATIBLE comparison AND no settlement-stage refusal; a row
        # that predates the recorded field reads as not established.
        "settlement": {"compatibility": scmp.get("compatibility"),
                       "overall_established": (
                           (scmp.get("overall_established") is True
                            and scmp.get("compatibility") == "COMPATIBLE"
                            and not settle_unmet) if scmp else None),
                       "unmet": settle_unmet,
                       "blockers": list(scmp.get("blockers") or []),
                       "fixture_read": scmp.get("fixture_read"),
                       "game_pk": scmp.get("fixture_game_pk"),
                       "official_date": None, "home_team": None,
                       "away_team": None},
        "payout_binding_ok": None,
        "admissible": bool(r.get("admissible")),
        "refusals": refusals,
        "plan_input": plan_input,
        "decided_at": decided,
    }


# ═════════════════════════════════════════════════════════════════════════
# THE ECONOMICS
# ═════════════════════════════════════════════════════════════════════════

def walk(levels, qty) -> list:
    """[(price, qty)] consumed in ladder order up to `qty`."""
    out, left = [], float(qty)
    for lv in levels or []:
        if left <= 1e-12:
            break
        px = _f(lv.get("price") if isinstance(lv, dict) else None)
        q = _f(lv.get("qty") if isinstance(lv, dict) else None)
        if px is None or q is None or q <= 0:
            continue
        take = min(q, left)
        out.append((px, take))
        left -= take
    if left > 1e-9:
        return []
    return out


def blend(p_internal, p_pinnacle) -> float | None:
    """V2's blended probability: (internal + Pinnacle) / 2, as the DECIMAL
    mean of the two recorded numbers. None when either is missing -- nothing
    is ever averaged with a missing value."""
    a, b = _dec(p_internal), _dec(p_pinnacle)
    if a is None or b is None:
        return None
    return float((a + b) / 2)


def economics(*, p_pinnacle, p_model, fills, fee_fn=None, at=None,
              params=None, policy: str = ACTIVE_POLICY) -> dict:
    """The owner's arithmetic, in explicit units. Pure.

    `fills` is [(price_usd_per_contract, qty_contracts)] -- the depth walk the
    order would consume. Fees are charged per fill by `fee_fn` (default
    `bettor_funded_book.fee_for`, the function the funded book books with).
    The HEADLINE is the probability `policy` binds: V2 the blended average
    (None when either input is missing), V1 the lower of the two.
    """
    prm = dict(DEFAULT_PARAMS, **(params or {}))
    out: dict[str, Any] = {"params": prm, "units": dict(PARAM_UNITS)}
    qty = sum(q for _, q in fills)
    cost = sum(px * q for px, q in fills)
    if not fills or qty <= 0:
        return dict(out, ok=False, refusal=R_NO_QTY)
    vwap = cost / qty
    out.update(qty=qty, acquisition_cost_usd=round(cost, 9),
               executable_price=round(vwap, 9),
               executable_price_basis="DEPTH_WEIGHTED_OVER_THE_WALKED_QTY")
    if fee_fn is None:
        from .. import bettor_funded_book as FB

        def fee_fn(q, px):
            return FB.fee_for(q, px, at=at)
    fees, fee_basis = 0.0, []
    try:
        for px, q in fills:
            got = fee_fn(q, px)
            charge, basis = (got if isinstance(got, tuple) else (got, None))
            fees += abs(float(charge))
            fee_basis.append({"price": px, "qty": q, "fee_usd": float(charge),
                              "basis": basis})
        fees_ok = True
    except Exception as exc:                                   # noqa: BLE001
        fees_ok = False
        fee_basis.append({"error": "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160])})
    out["fees_usd"] = round(fees, 9) if fees_ok else None
    out["fee_basis"] = fee_basis
    out["fees_ok"] = fees_ok
    #: THE DEPTH WALK ITSELF, so a replay re-prices exactly this quantity.
    out["fills"] = [[float(px), float(q)] for px, q in fills]

    def _one(p):
        return edge_figures(p, fills=fills, fees=(fees if fees_ok else None),
                            threshold=prm["min_gross_edge_pp"])
    out["pinnacle"] = _one(p_pinnacle)
    out["model"] = _one(p_model)
    p_blend = blend(p_model, p_pinnacle)
    out["blended"] = _one(p_blend)
    out["policy"] = policy
    if policy == POLICY_V1:
        ps = [x for x in (p_pinnacle, p_model) if x is not None]
        # V1: THE HEADLINE IS THE LOWER ESTIMATE, the one V1 guarantees.
        out["headline"] = _one(min(ps)) if ps else None
        out["headline_is"] = ("V1: THE_LOWER_OF_THE_TWO_QUALIFIED_PROBABILITIES "
                              "(%s)" % COMBINATION_V1)
    else:
        # V2: THE HEADLINE IS THE BLENDED AVERAGE, and only when both exist.
        out["headline"] = out["blended"]
        out["headline_is"] = ("V2: THE_BLENDED_AVERAGE (internal + Pinnacle) "
                              "/ 2 of the figures supplied; absent when "
                              "either is absent (%s). The BINDING figures, "
                              "with each input's qualification, are "
                              "decide_entry's (evidence.policy_decision)"
                              % COMBINATION_V2)
    out["roi_denominator"] = ("acquisition cost + fees: the cash the entry "
                              "deploys")
    return dict(out, ok=True, refusal=None)


def edge_figures(p, *, fills, fees, threshold) -> dict | None:
    """One probability's figures over one depth walk. Pure. `fees` is the
    fee total in dollars, or None when the schedule could not price it."""
    if p is None or not fills:
        return None
    qty = sum(q for _, q in fills)
    cost = sum(px * q for px, q in fills)
    vwap = cost / qty
    e = gross_edge(p, vwap)
    gross = sum(q * (float(p) - px) for px, q in fills)
    net = (gross - fees) if fees is not None else None
    return {"p": float(p), "gross_edge_pp": e,
            "gross_edge_percentage_points": (None if e is None
                                             else round(e * 100.0, 9)),
            "clears_min_gross_edge": clears(e, threshold),
            "expected_gross_profit_usd": round(gross, 9),
            "expected_gross_return_on_cost": (round(gross / cost, 9)
                                              if cost > 0 else None),
            "expected_net_profit_usd": (None if net is None
                                        else round(net, 9)),
            "expected_net_roi": (None if net is None or cost + fees <= 0
                                 else round(net / (cost + fees), 9))}


# ═════════════════════════════════════════════════════════════════════════
# THE COMBINATION RULE: ONE PURE FUNCTION FOR EXECUTION AND FOR DISPLAY
# ═════════════════════════════════════════════════════════════════════════

NOT_EVALUATED = "NOT_EVALUATED"
#: V2's conditions, in order.
COND_INPUTS = "internal_and_pinnacle_present_and_qualified"
COND_QTY = "priced_quantity_and_depth_walk"
COND_BLENDED_EDGE = "blended_gross_edge_at_least_min_gross_edge_pp"
#: V1's conditions, in order (replay only).
COND_PIN_EDGE = "pinnacle_gross_edge_at_least_min_gross_edge_pp"
COND_MODEL_PRESENT = "approved_internal_model_present"
COND_MODEL_EDGE = "internal_gross_edge_at_least_min_gross_edge_pp"
#: Both policies' net conditions (a separate check, C_NET_EV).
COND_NET_POSITIVE = "expected_net_profit_after_fees_positive"
COND_NET_MIN = "expected_net_profit_at_least_min_net_ev_usd"
NET_CONDITIONS = (COND_NET_POSITIVE, COND_NET_MIN)

_DEPENDENCY_OF = {
    R_NO_QTY: DEP_EVIDENCE, R_NO_PINNACLE: DEP_EVIDENCE, R_STALE: DEP_EVIDENCE,
    R_FRESHNESS_UNKNOWN: DEP_EVIDENCE, R_FEES: DEP_EVIDENCE,
    R_NO_MODEL: DEP_ENGINEERING, R_MODEL_CANNOT_SCORE: DEP_ENGINEERING,
    R_BELOW: DEP_NONE_MARKET, R_DISAGREE: DEP_NONE_MARKET,
    R_NET: DEP_NONE_MARKET, R_BELOW_NET: DEP_NONE_MARKET}


def policy_use_is(policy: str = ACTIVE_POLICY) -> str:
    """WHAT THE POLICY'S COMBINATION OF THE TWO ESTIMATES IS, stated wherever
    the policy is shown. Neither version is independent confirmation."""
    from .. import bettor_funded_model as FM
    if policy == POLICY_V1:
        return FM.ENTRY_POLICY_V1_AGREEMENT_IS
    return FM.ENTRY_POLICY_AGREEMENT_IS


def _cond(name, status, *, refusal=None, detail=None, **ev) -> dict:
    return dict({"condition": name, "status": status,
                 "passed": (None if status == NOT_EVALUATED
                            else status == PASS),
                 "refusal": None if status in (PASS, NOT_EVALUATED)
                 else refusal,
                 "dependency": (None if status in (PASS, NOT_EVALUATED)
                                else _DEPENDENCY_OF.get(refusal,
                                                        DEP_EVIDENCE)),
                 "detail": detail}, **ev)


#: THE STABLE FIELD NAMES OF THE STORED POLICY DECISION. A UI displays these
#: verbatim from the record; renaming one is a breaking change.
RECORD_FIELDS = (
    "policy_name", "policy_version", "p_internal", "internal_model_version",
    "internal_at", "p_pinnacle", "pinnacle_at", "p_blended", "gross_edge_pp",
    "fees_usd", "net_expected_profit_usd", "expected_return_pct",
    "conditions", "rationale", "instrument")

#: THE INSTRUMENT LABEL'S INPUTS (the record's `instrument`), for a shared
#: label resolver. Each is taken from the verified metadata the valuation
#: used; an unknown one is None with its reason in `instrument.unknown`.
INSTRUMENT_FIELDS = (
    "participant", "pays_on", "home_team", "away_team", "market_type",
    "line", "side", "period", "competition", "event_date", "event_title",
    "us_market_slug", "event_key")


def instrument_label(cand: dict, *, catalogue_row: dict | None = None,
                     fixture_row: dict | None = None) -> dict:
    """WHAT WAS DECIDED ON, IN A MANAGER'S TERMS -- the inputs, not a
    rendered label. Pure. Sources: the contract identity the venue resolver
    established (selection, side, period, market, line), the fixture
    metadata the settlement comparison read (teams, official date), and the
    catalogue row (event title, the league token of the venue's event
    slug). Nothing is guessed: an unknown field is None with a reason."""
    st = dict(cand.get("settlement") or {})
    fx = dict(fixture_row or {}) if st.get("fixture_read") is True else {}
    cat = dict(catalogue_row or {})
    vals: dict[str, Any] = {}
    basis: dict[str, str] = {}
    unknown: dict[str, str] = {}

    def put(k, v, src, why):
        if v is None or v == "":
            vals[k] = None
            unknown[k] = why
        else:
            vals[k] = v
            basis[k] = src
    put("participant", cand.get("selection"),
        "contract.selection (venue identity resolver)",
        "the contract carries no selection")
    put("pays_on", cand.get("payout_event"),
        "contract payout event (bound to the venue outcome)",
        "the payout event is not recorded")
    put("home_team", st.get("home_team") or fx.get("home_team"),
        "fixture_metadata read by the settlement comparison",
        "no fixture metadata was read for this valuation")
    put("away_team", st.get("away_team") or fx.get("away_team"),
        "fixture_metadata read by the settlement comparison",
        "no fixture metadata was read for this valuation")
    mk = cand.get("market")
    put("market_type", ("MONEYLINE" if mk == "h2h" else mk),
        "contract.market (%s)" % mk, "the contract carries no market")
    put("line", cand.get("line"), "contract.line",
        "a moneyline has no line" if mk == "h2h"
        else "the contract carries no line")
    put("side", cand.get("side"), "the order intent the resolver bound",
        "no order intent is recorded")
    put("period", cand.get("period"),
        "contract.period (%s)" % cand.get("period_basis"),
        "the period was not established")
    slug = str(cat.get("event_slug") or "")
    league = slug.split("-", 1)[0].upper() if "-" in slug else None
    put("competition", league,
        "the league token of the venue's event slug (us_premap.event_slug)",
        "no catalogue event slug names the competition")
    ed = st.get("official_date") or fx.get("official_date")
    put("event_date", (None if ed is None else
                       ed.isoformat() if hasattr(ed, "isoformat")
                       else str(ed)),
        "fixture_metadata.official_date read by the settlement comparison",
        "no fixture metadata was read for this valuation")
    put("event_title", cat.get("event_title"), "us_premap.event_title",
        "no catalogue row for this market")
    put("us_market_slug", cand.get("us_market_slug"), "contract identity",
        "no venue market slug")
    put("event_key", cand.get("event_key"), "the book's event id",
        "no event key")
    return dict(vals, basis=basis, unknown=unknown)

#: ── THE SETTLEMENT STATES OF ONE BOUGHT CONTRACT (V2's net EV) ──────────
#: The payout-state distribution's own convention (bettor_payout_states /
#: xavier_ladder.measure_of): P(WIN) = (1-v) p, P(LOSE) = (1-v)(1-p),
#: P(VOID) = v, with p conditional on the fixture being played (both the
#: de-vigged Pinnacle price and the internal model's labels exclude voids)
#: and v the measured void rate (bettor_pair_observations.void_rate). A
#: contract the venue voids returns its purchase price (the settlement terms
#: are attested COMPATIBLE with the book's "void, stakes returned" rule before
#: any entry), so the VOID state adds no gross profit; the fees are NOT
#: assumed refunded.
VOID_MEASURED = "MEASURED"
VOID_UNMEASURED = "THE_VOID_RATE_IS_NOT_MEASURED_PROBABILITIES_ARE_CONDITIONAL"
VOID_PAYOUT_UNESTABLISHED = "THE_VOID_PAYOUT_IS_NOT_ESTABLISHED"


def settlement_states(void: dict | None, *,
                      void_refunds_price: bool | None) -> dict:
    """The void measure a V2 valuation applies. Pure."""
    vr = dict(void or {})
    v = _f(vr.get("rate")) if vr.get("ok") else None
    vu = _f(vr.get("upper_95")) if vr.get("ok") else None
    if v is not None and not (0.0 <= v < 1.0):
        v = vu = None
    status = (VOID_PAYOUT_UNESTABLISHED if void_refunds_price is False
              else VOID_MEASURED if v is not None else VOID_UNMEASURED)
    return {"void_rate": v if status == VOID_MEASURED else None,
            "void_upper_95": vu if status == VOID_MEASURED else None,
            "void_n_fixtures": vr.get("n_fixtures"),
            "void_source": vr.get("source"),
            "void_refusal": vr.get("refusal"),
            "void_status": status,
            "void_payout": ("REFUND_OF_THE_PURCHASE_PRICE (settlement "
                            "attested COMPATIBLE); fees not assumed refunded"
                            if void_refunds_price else
                            "NOT_ESTABLISHED" if void_refunds_price is False
                            else "REFUND_OF_THE_PURCHASE_PRICE (assumed with "
                                 "the attested settlement terms)"),
            "convention": ("P(WIN)=(1-v)p, P(LOSE)=(1-v)(1-p), P(VOID)=v; "
                           "expected gross = (1-v) x sum q (p - price); "
                           "net = gross - fees"),
            "applied": status == VOID_MEASURED,
            "basis": ("the measured void rate is applied" if
                      status == VOID_MEASURED else
                      "no measured void rate: the figures are CONDITIONAL on "
                      "the fixture being played, and say so")}


def _pct(v):
    return None if v is None else round(float(v) * 100.0, 9)


def rationale(pd: dict, *, verdict: str | None = None,
              refusal: str | None = None) -> str:
    """ONE CONCISE SENTENCE BUILT ONLY FROM THE STORED FIELDS of a policy
    decision (and, when known, Derek's overall verdict). Pure."""
    def n(v, f="%.4f"):
        return "n/a" if v is None else f % v
    pol = pd.get("policy_name")
    head = ("%s under %s (%s)" % (verdict, pol, pd.get("policy_version"))
            if verdict else "%s (%s)" % (pol, pd.get("policy_version")))
    if refusal:
        head += " -- refused %s" % refusal
    if pol == POLICY_V2:
        probs = ("p_blended %s = (p_internal %s [%s] + p_pinnacle %s) / 2"
                 % (n(pd.get("p_blended")), n(pd.get("p_internal")),
                    pd.get("internal_model_version") or "no approved model",
                    n(pd.get("p_pinnacle"))))
    else:
        probs = ("p_internal %s [%s], p_pinnacle %s, each must clear; "
                 "headline the lower %s" % (
                     n(pd.get("p_internal")),
                     pd.get("internal_model_version") or "no approved model",
                     n(pd.get("p_pinnacle")),
                     n(pd.get("policy_probability"))))
    econ = ("at price %s: gross_edge_pp %s vs %s pp; fees_usd %s; "
            "net_expected_profit_usd %s; expected_return_pct %s"
            % (n(pd.get("executable_price")), n(pd.get("gross_edge_pp"),
                                                "%.2f"),
               n(pd.get("threshold_gross_edge_pp"), "%.2f"),
               n(pd.get("fees_usd"), "%.2f"),
               n(pd.get("net_expected_profit_usd"), "%.2f"),
               n(pd.get("expected_return_pct"), "%.2f")))
    conds = ", ".join("%s=%s" % (c["condition"], c["status"])
                      for c in pd.get("conditions") or [])
    return "%s: %s; %s. Conditions: %s." % (head, probs, econ, conds)


def decide_entry(policy: str, *, internal: dict, pinnacle: dict, econ: dict,
                 params: dict | None = None, void: dict | None = None,
                 void_refunds_price: bool | None = None,
                 policy_version: str | None = None) -> dict:
    """THE COMBINATION RULE OF `policy` ON ONE PRICED CANDIDATE. Pure.

    THE ONE FUNCTION: `evaluate` (and so the funded gate and the scheduled
    pass) takes its combination and net-EV verdicts from this output, the
    decision record persists it whole (evidence.policy_decision, and the
    headline columns are copied from it), and Derek's workspace displays the
    stored copy -- nothing downstream recomputes it. The stable field names
    are RECORD_FIELDS.

      internal  {p, qualified, refusal, why, model_id, model_version, at}
                -- qualified means the registry's approved model scored it
      pinnacle  {p, qualified, qualification, refusal, why, at}
                -- qualified means the lane's freshness rule passed
      econ      `economics(...)` over the depth walk (fills, fees)
      void      `bettor_pair_observations.void_rate(...)` (V2's settlement
                states); absent or unmeasured -> conditional, and labelled

    V2: p_blended = (p_internal + p_pinnacle) / 2, only when BOTH are present
    and qualified; enter iff p_blended - executable price >= min_gross_edge_pp
    AND expected net profit after fees (settlement states applied) > 0 and
    >= min_net_ev_usd. V1: each estimate must clear the threshold; net on the
    lower of the two (V1 never applied settlement states).
    """
    if policy not in POLICY_RULES:
        raise ValueError("unknown Derek entry policy %r" % (policy,))
    prm = dict(DEFAULT_PARAMS, **(params or {}))
    thr = float(prm["min_gross_edge_pp"])
    internal, pinnacle, econ = (dict(internal or {}), dict(pinnacle or {}),
                                dict(econ or {}))
    ok = bool(econ.get("ok"))
    fills = ([(float(px), float(q)) for px, q in (econ.get("fills") or [])]
             if ok else [])
    priced = ok and bool(fills)
    fees = econ.get("fees_usd") if (ok and econ.get("fees_ok")) else None
    states = (settlement_states(void, void_refunds_price=void_refunds_price)
              if policy == POLICY_V2 else
              {"applied": False, "void_status": "NOT_PART_OF_V1"})
    scale = (1.0 - states["void_rate"]) if states.get("applied") else 1.0

    def fig(p):
        return edge_figures(p, fills=fills, fees=fees, threshold=thr) \
            if priced else None

    p_int = _f(internal.get("p")) if internal.get("qualified") else None
    p_pin = _f(pinnacle.get("p"))
    conds: list = []
    blended = None
    if policy == POLICY_V2:
        pin_ok = p_pin is not None and pinnacle.get("qualified") is True
        if p_pin is None:
            conds.append(_cond(COND_INPUTS, FAIL, refusal=R_NO_PINNACLE,
                               detail="no de-vigged Pinnacle probability; "
                                      "nothing is averaged with a missing "
                                      "value"))
        elif not pin_ok:
            ref = pinnacle.get("refusal") or R_NO_PINNACLE
            conds.append(_cond(
                COND_INPUTS, UNKNOWN if ref == R_FRESHNESS_UNKNOWN else FAIL,
                refusal=ref,
                detail="the Pinnacle probability %.6f is not qualified (%s); "
                       "it is not averaged" % (p_pin, pinnacle.get("why")
                                               or ref)))
        elif p_int is None:
            ref = internal.get("refusal") or R_NO_MODEL
            conds.append(_cond(
                COND_INPUTS, FAIL, refusal=ref,
                detail="no qualified internal probability (%s): %s; there "
                       "is no Pinnacle-only fallback and no substitute "
                       "estimate" % (ref, internal.get("why") or "the "
                                     "registry has no approved model that "
                                     "scored this candidate")))
        else:
            blended = blend(p_int, p_pin)
            conds.append(_cond(COND_INPUTS, PASS,
                               detail="internal %.6f (%s) and Pinnacle %.6f "
                                      "(%s) both qualified" % (
                                          p_int, internal.get("model_version"),
                                          p_pin, pinnacle.get("qualification")
                                          or "FRESH")))
        conds.append(_cond(COND_QTY, PASS if priced else UNKNOWN,
                           refusal=R_NO_QTY,
                           detail=("%s contracts walked at $%.6f "
                                   "depth-weighted" % (econ.get("qty"),
                                                       econ.get(
                                                           "executable_price"))
                                   if priced else
                                   "no priced quantity to walk")))
        head = fig(blended)
        if head is None:
            conds.append(_cond(COND_BLENDED_EDGE, NOT_EVALUATED,
                               detail="no blended probability over a priced "
                                      "quantity"))
        else:
            conds.append(_cond(
                COND_BLENDED_EDGE,
                PASS if head["clears_min_gross_edge"] else FAIL,
                refusal=R_BELOW,
                detail="blended %.6f - price %.6f = %.4f pp %s %.4f pp" % (
                    blended, econ.get("executable_price"),
                    head["gross_edge_percentage_points"],
                    ">=" if head["clears_min_gross_edge"] else "<",
                    thr * 100.0),
                value=head["gross_edge_percentage_points"],
                threshold=thr * 100.0, units="percentage points"))
        headline_is = ("V2: THE_BLENDED_AVERAGE (internal + Pinnacle) / 2 of "
                       "two QUALIFIED estimates")
    else:
        pe, me = fig(p_pin), fig(p_int)
        if pe is None:
            conds.append(_cond(COND_PIN_EDGE, UNKNOWN, refusal=R_NO_QTY,
                               detail="no edge could be computed (no priced "
                                      "quantity or no Pinnacle probability)"))
        else:
            conds.append(_cond(COND_PIN_EDGE,
                               PASS if pe["clears_min_gross_edge"] else FAIL,
                               refusal=R_BELOW,
                               detail="Pinnacle edge %.4f pp vs %.4f pp" % (
                                   pe["gross_edge_percentage_points"],
                                   thr * 100.0),
                               value=pe["gross_edge_percentage_points"],
                               threshold=thr * 100.0,
                               units="percentage points"))
        conds.append(_cond(COND_MODEL_PRESENT,
                           PASS if p_int is not None else UNKNOWN,
                           refusal=R_NO_MODEL,
                           detail=("%s scored it" % internal.get(
                               "model_version") if p_int is not None else
                               "no approved model estimate to agree with "
                               "Pinnacle")))
        if me is None:
            conds.append(_cond(COND_MODEL_EDGE, NOT_EVALUATED,
                               detail="no model edge over a priced quantity"))
        else:
            conds.append(_cond(COND_MODEL_EDGE,
                               PASS if me["clears_min_gross_edge"] else FAIL,
                               refusal=R_DISAGREE,
                               detail="model edge %.4f pp vs %.4f pp" % (
                                   me["gross_edge_percentage_points"],
                                   thr * 100.0),
                               value=me["gross_edge_percentage_points"],
                               threshold=thr * 100.0,
                               units="percentage points"))
        ps = [x for x in (p_pin, p_int) if x is not None]
        head = fig(min(ps)) if ps else None
        headline_is = "V1: THE_LOWER_OF_THE_TWO_PROBABILITIES"
    # ── THE HEADLINE ECONOMICS, settlement states applied (V2) ───────
    cost = econ.get("acquisition_cost_usd") if priced else None
    gross = (None if head is None
             else round(head["expected_gross_profit_usd"] * scale, 9))
    net = (None if gross is None or fees is None
           else round(gross - fees, 9))
    total = None if cost is None or fees is None else round(cost + fees, 9)
    roc = (None if net is None or not total or total <= 0
           else round(net / total, 9))
    net_upper = None
    if head is not None and fees is not None and \
            states.get("void_upper_95") is not None:
        net_upper = round(head["expected_gross_profit_usd"]
                          * (1.0 - states["void_upper_95"]) - fees, 9)
    # ── NET EV AFTER FEES (both policies), on the policy's headline ──
    if head is None and policy == POLICY_V2:
        first = next((c for c in conds if c["status"] not in
                      (PASS, NOT_EVALUATED)), None)
        why = (first or {}).get("refusal") or R_NO_QTY
        conds.append(_cond(COND_NET_POSITIVE, UNKNOWN, refusal=why,
                           detail="net EV not computable: no blended "
                                  "probability over a priced quantity"))
        conds.append(_cond(COND_NET_MIN, NOT_EVALUATED))
    elif net is None:
        conds.append(_cond(COND_NET_POSITIVE, UNKNOWN,
                           refusal=(R_FEES if ok else R_NO_QTY),
                           detail="net EV not computable"))
        conds.append(_cond(COND_NET_MIN, NOT_EVALUATED))
    else:
        conds.append(_cond(COND_NET_POSITIVE, PASS if net > 0 else FAIL,
                           refusal=R_NET,
                           detail="expected net $%.6f after $%.4f fees%s%s"
                                  % (net, fees,
                                     "" if not states.get("applied") else
                                     " (void rate %.4f applied)"
                                     % states["void_rate"],
                                     "" if net > 0 else " is not positive"),
                           value=net, threshold=0.0, units="USD"))
        mn = float(prm["min_net_ev_usd"])
        conds.append(_cond(COND_NET_MIN, PASS if net >= mn else FAIL,
                           refusal=R_BELOW_NET,
                           detail="expected net $%.6f %s min_net_ev_usd $%.4f"
                                  % (net, ">=" if net >= mn else "<", mn),
                           value=net, threshold=mn, units="USD"))
    failing = [c for c in conds if c["status"] not in (PASS, NOT_EVALUATED)]
    qty = econ.get("qty") if priced else None
    per = (lambda v: None if v is None or not qty else round(v / qty, 9))
    e_frac = (head or {}).get("gross_edge_pp")
    pd = {
        # ── THE STABLE FIELDS (RECORD_FIELDS) ────────────────────────
        "policy_name": policy,
        "policy_version": policy_version or policy,
        "p_internal": _f(internal.get("p")),
        "internal_model_version": internal.get("model_version"),
        "internal_at": internal.get("at"),
        "p_pinnacle": p_pin,
        "pinnacle_at": pinnacle.get("at"),
        "p_blended": blended,
        #: PERCENTAGE POINTS (9.0 == 9 pp). NB the table column and the
        #: parameter of the same name hold the FRACTION (0.09); this record
        #: states its unit in `units` and carries the fraction beside it.
        "gross_edge_pp": _pct(e_frac),
        "fees_usd": fees,
        "net_expected_profit_usd": net,
        "expected_return_pct": _pct(roc),
        "conditions": conds,
        "rationale": None,
        "instrument": None,
        # ── SUPPORTING FIELDS ─────────────────────────────────────────
        "function": "agents.derek_policy.decide_entry",
        "policy_key": POLICY_KEY,
        "combination_policy": POLICY_RULES[policy],
        "inputs_are": policy_use_is(policy),
        "internal_qualified": bool(internal.get("qualified")),
        "internal_refusal": internal.get("refusal"),
        "internal_model_id": internal.get("model_id"),
        "internal_gross_edge_pp": _pct((fig(p_int) or {}).get(
            "gross_edge_pp")),
        "pinnacle_qualified": pinnacle.get("qualified") is True,
        "pinnacle_qualification": pinnacle.get("qualification"),
        "pinnacle_refusal": pinnacle.get("refusal"),
        "pinnacle_gross_edge_pp": _pct((fig(p_pin) or {}).get(
            "gross_edge_pp")),
        "policy_probability": (head or {}).get("p"),
        "policy_probability_is": headline_is,
        "executable_price": econ.get("executable_price") if priced else None,
        "executable_price_basis": econ.get("executable_price_basis"),
        "qty": qty,
        "acquisition_cost_usd": cost,
        "gross_edge_fraction": e_frac,
        "threshold_gross_edge_pp": thr * 100.0,
        "threshold_gross_edge_fraction": thr,
        "edge_tolerance_fraction": EDGE_TOLERANCE_PP,
        "expected_gross_profit_usd": gross,
        "expected_gross_profit_per_contract_usd": per(gross),
        "expected_gross_return_pct": (None if gross is None or not cost
                                      else _pct(gross / cost)),
        "fee_per_contract_usd": per(fees),
        "total_cost_usd": total,
        "net_expected_profit_per_contract_usd": per(net),
        "net_expected_profit_at_void_upper_95_usd": net_upper,
        "expected_return_on_capital": roc,
        "return_on_capital_denominator": ("acquisition cost (price x qty) + "
                                          "fees: the cash the entry deploys"),
        "min_net_ev_usd": float(prm["min_net_ev_usd"]),
        "settlement_states": states,
        "units": {"p_*": "probability of the payout event (0..1)",
                  "gross_edge_pp": "PERCENTAGE POINTS (9.0 == 9 pp)",
                  "gross_edge_fraction": "fraction (0.09 == 9 pp)",
                  "*_usd": "US dollars for qty contracts unless per_contract",
                  "expected_return_pct": ("percent: net / (acquisition cost"
                                          " + fees) x 100"),
                  "expected_gross_return_pct": ("percent: gross / acquisition"
                                                " cost x 100, before fees"),
                  "internal_at / pinnacle_at": "epoch seconds (UTC)"},
        "admitted": not failing,
        "refusal": failing[0]["refusal"] if failing else None,
        "refusals": [c["refusal"] for c in failing],
    }
    pd["rationale"] = rationale(pd)
    return pd


def combination_check(pd: dict) -> dict:
    """The policy's combination check, built from `decide_entry`'s output."""
    policy = pd["policy_name"]
    name = COMBINATION_CHECK[policy]
    mine = [c for c in pd["conditions"]
            if c["condition"] not in NET_CONDITIONS]
    bad = next((c for c in mine if c["status"] not in (PASS, NOT_EVALUATED)),
               None)
    ev = {"combination": pd["combination_policy"], "policy": policy,
          "min_gross_edge_pp": pd["threshold_gross_edge_fraction"],
          "tolerance_pp": EDGE_TOLERANCE_PP,
          "p_internal": pd["p_internal"], "p_pinnacle": pd["p_pinnacle"],
          "p_blended": pd["p_blended"],
          "gross_edge_fraction": pd["gross_edge_fraction"],
          "pinnacle_gross_edge_pp": pd["pinnacle_gross_edge_pp"],
          "model_gross_edge_pp": pd["internal_gross_edge_pp"],
          "conditions": mine}
    if bad is None:
        return _check(name, PASS, "; ".join(
            c["detail"] for c in mine if c.get("detail")),
            source="agents.derek_policy.decide_entry", evidence=ev)
    return _check(name, bad["status"], bad["detail"], refusal=bad["refusal"],
                  dependency=bad["dependency"],
                  source="agents.derek_policy.decide_entry", evidence=ev)


def net_ev_check(pd: dict) -> dict:
    """C_NET_EV, built from `decide_entry`'s output."""
    mine = [c for c in pd["conditions"] if c["condition"] in NET_CONDITIONS]
    bad = next((c for c in mine if c["status"] not in (PASS, NOT_EVALUATED)),
               None)
    ev = {"expected_net_profit_usd": pd["net_expected_profit_usd"],
          "min_net_ev_usd": pd["min_net_ev_usd"], "fees_usd": pd["fees_usd"],
          "settlement_states": pd["settlement_states"],
          "policy": pd["policy_name"], "conditions": mine}
    if bad is None:
        return _check(C_NET_EV, PASS, mine[0]["detail"],
                      source="agents.derek_policy.decide_entry", evidence=ev)
    return _check(C_NET_EV, bad["status"], bad["detail"],
                  refusal=bad["refusal"], dependency=bad["dependency"],
                  source="agents.derek_policy.decide_entry", evidence=ev)


# ═════════════════════════════════════════════════════════════════════════
# THE INTERNAL MODEL
# ═════════════════════════════════════════════════════════════════════════

async def approved_entry_model(conn) -> dict:
    """The registry's own reader, with provenance verification. Never raises."""
    from .. import bettor_funded_model as FM
    try:
        return await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": FM.R_SCHEMA_UNAVAILABLE,
                "error": type(exc).__name__}


def model_estimate(approved: dict | None, cand: dict, *,
                   at: float | None) -> dict:
    """Score the approved model on the candidate's decision-time vector.
    Pure given the registry read. A model approved after the decision instant
    was not available to it and is not used."""
    from .. import bettor_funded_model as FM
    ap = dict(approved or {})
    feats = entry_features(cand)
    out: dict[str, Any] = {"model_key": FM.KEY_ENTRY_PAYOUT,
                           "features": feats,
                           "feature_sha": (None if feats is None
                                           else FM.feature_sha(feats))}
    if not ap.get("ok"):
        return dict(out, ok=False, refusal=R_NO_MODEL,
            registry_refusal=ap.get("refusal"),
            why=("the registry has no APPROVED, provenance-verified model for "
                 "%s (%s). The entry policy needs both a qualified internal "
                 "and a qualified Pinnacle probability; Pinnacle alone is "
                 "never enough" % (FM.KEY_ENTRY_PAYOUT, ap.get("refusal"))))
    m = dict(ap.get("model") or {})
    approved_at = _epoch(m.get("approved_at"))
    if at is not None and approved_at is not None and approved_at > at:
        return dict(out, ok=False, refusal=R_NO_MODEL,
                    registry_refusal="APPROVED_AFTER_THE_DECISION_INSTANT",
                    why=("model %s was approved at %s, after this decision "
                         "(%s)" % (m.get("model_id"), approved_at, at)))
    base = {"model_id": m.get("model_id"),
            "model_version": m.get("model_version"),
            "approved_by": m.get("approved_by"),
            "approved_at": approved_at,
            "provenance_verified": bool(ap.get("provenance_verified"))}
    if feats is None:
        return dict(out, **base, ok=False, refusal=R_MODEL_CANNOT_SCORE,
                    why="no executable price, so the model has no input")
    need = list(m.get("features") or [])
    missing = [f for f in need if f not in feats]
    if missing:
        return dict(out, **base, ok=False, refusal=R_MODEL_CANNOT_SCORE,
                    missing_features=missing)
    try:
        p = float(FM.load(m["params"]).predict(feats))
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, **base, ok=False, refusal=R_MODEL_CANNOT_SCORE,
                    error=type(exc).__name__)
    ev = dict(m.get("evaluation") or {})
    comp = dict(ev.get("promotion_comparison") or {})
    pros = dict(ev.get("PROSPECTIVE") or {})
    mc = dict(comp.get("market_comparison") or {})
    return dict(out, **base, ok=True, refusal=None, p=p,
                description=FM.ENTRY_PAYOUT_DESCRIPTION,
                promotion_vs_raw_price=(mc.get("model_vs_raw_venue_price")
                                        or {}).get("log_loss_improvement"),
                uncertainty={
                    "kind": "REGISTRY_EVALUATION_NOT_A_PREDICTION_INTERVAL",
                    "prospective_n_events": pros.get("n_events"),
                    "prospective_log_loss": pros.get("log_loss"),
                    "promotion_log_loss": comp.get("candidate"),
                    "promotion_baseline_log_loss": comp.get("against"),
                    "promotion_improvement": comp.get("improvement")})


# ═════════════════════════════════════════════════════════════════════════
# THE EXECUTION AUTHORITY'S STATE (recorded; enforced by the connector)
# ═════════════════════════════════════════════════════════════════════════

async def authority_checks(conn, *, now: float) -> list:
    """Account, owner limits/authorization and the submission switches, read
    through the modules that own them. Never raises: a failed read is UNKNOWN."""
    from .. import bettor_entry_execution as EX
    from .. import bettor_funded_activation as FA
    from .. import bettor_funded_execution as FX
    out = []
    # ACCOUNT AND RECONCILIATION
    try:
        bound = FA._obj(await FA._state(conn, FA.ACCOUNT_KEY)) or {}
        acct = str(bound.get("account_id") or "").strip()
        if not acct:
            out.append(_check(
                C_ACCOUNT, FAIL, "no funded account is bound",
                blocks=BLOCKS_EXECUTION, refusal="NO_FUNDED_ACCOUNT_BOUND",
                dependency=DEP_OWNER_DECISION,
                source="bettor_funded_activation.ACCOUNT_KEY"))
        else:
            sel = await FA.account_selection(conn, acct)
            ok = bool(sel.get("ok"))
            out.append(_check(
                C_ACCOUNT, PASS if ok else FAIL,
                sel.get("why") or ("account %s selectable" % acct),
                blocks=BLOCKS_EXECUTION, refusal=sel.get("refusal"),
                dependency=DEP_OWNER_DECISION,
                source="bettor_funded_activation.account_selection",
                evidence={"account_id": acct,
                          "refusal": sel.get("refusal")}))
    except Exception as exc:                                   # noqa: BLE001
        out.append(_check(C_ACCOUNT, UNKNOWN,
                          "the account read failed: %s" % type(exc).__name__,
                          blocks=BLOCKS_EXECUTION,
                          refusal="ACCOUNT_READ_FAILED",
                          dependency=DEP_ENGINEERING,
                          source="bettor_funded_activation"))
    # OWNER LIMITS, RESERVATIONS AND AUTHORIZATION
    try:
        approved = await FX._approved(conn)
        auth = FA._obj(await FA._state(conn, FA.AUTHORIZATION_KEY))
        ok = bool(approved) and bool(auth)
        out.append(_check(
            C_LIMITS, PASS if ok else FAIL,
            ("owner-approved limits and an authorization record are present; "
             "the connector consumes the authorization and checks every rail "
             "and reservation at send time") if ok else
            ("%s%s" % ("" if approved else "no owner-approved limit set; ",
                       "" if auth else "no funded authorization record")),
            blocks=BLOCKS_EXECUTION,
            refusal=(None if ok else "OWNER_LIMITS_OR_AUTHORIZATION_ABSENT"),
            dependency=DEP_OWNER_DECISION,
            source="bettor_funded_execution._approved + "
                   "bettor_funded_activation.AUTHORIZATION_KEY",
            evidence={"limits_approved": bool(approved),
                      "authorization_recorded": bool(auth)}))
    except Exception as exc:                                   # noqa: BLE001
        out.append(_check(C_LIMITS, UNKNOWN,
                          "the limits read failed: %s" % type(exc).__name__,
                          blocks=BLOCKS_EXECUTION,
                          refusal="LIMITS_READ_FAILED",
                          dependency=DEP_ENGINEERING,
                          source="bettor_funded_execution"))
    # THE SUBMISSION SWITCHES -- read, never changed.
    sw = {"FUNDED_SUBMISSION_ENABLED": bool(FX.FUNDED_SUBMISSION_ENABLED),
          "REAL_ORDER_SUBMISSION_ENABLED":
              bool(EX.REAL_ORDER_SUBMISSION_ENABLED)}
    on = all(sw.values())
    out.append(_check(
        C_SUBMISSION, PASS if on else FAIL,
        ("both submission switches are on" if on else
         "submission is disabled in code (%s); funded authorization is the "
         "owner's decision and no agent changes it"
         % ", ".join(k for k, v in sw.items() if not v)),
        blocks=BLOCKS_EXECUTION, refusal=(None if on else
                                          "FUNDED_SUBMISSION_DISABLED"),
        dependency=DEP_OWNER_DECISION,
        source="bettor_funded_execution / bettor_entry_execution switches",
        evidence=sw))
    return out


# ═════════════════════════════════════════════════════════════════════════
# THE POLICY: ONE PURE FUNCTION
# ═════════════════════════════════════════════════════════════════════════

def _realism(cand: dict, catalogue_row: dict | None) -> dict:
    from .. import bettor_external_shadow as ext
    from .. import bettor_demonstration as demo
    from .. import bettor_venue_realism as vreal
    ev = {"experiment_id": cand.get("experiment_id"),
          "venue": cand.get("venue"),
          "fixture_read": cand["settlement"].get("fixture_read"),
          "game_pk": cand["settlement"].get("game_pk")}
    if cand.get("experiment_id") == demo.EXPERIMENT:
        return _check(C_REAL, FAIL, "a controlled demonstration record",
                      refusal=R_NOT_REAL, dependency=DEP_EVIDENCE,
                      source="bettor_demonstration.EXPERIMENT", evidence=ev)
    if cand.get("experiment_id") != ext.EXPERIMENT_ID:
        return _check(C_REAL, FAIL,
                      "not the entry lane's experiment (%s)"
                      % cand.get("experiment_id"), refusal=R_NOT_REAL,
                      dependency=DEP_EVIDENCE, source="external_valuations",
                      evidence=ev)
    if cand.get("venue") not in (None, "PMUS"):
        return _check(C_REAL, FAIL,
                      "not a Polymarket US contract (%s); an international "
                      "contract is never substituted" % cand.get("venue"),
                      refusal=R_NOT_REAL, dependency=DEP_EVIDENCE,
                      source="contract.venue", evidence=ev)
    cls = vreal.classify(catalogue_row) if catalogue_row else None
    ev["catalogue_realism"] = (cls or {}).get("verdict")
    if cls and cls.get("verdict") == vreal.SIMULATED:
        return _check(C_REAL, FAIL, "the venue classifies it simulated",
                      refusal=R_NOT_REAL, dependency=DEP_EVIDENCE,
                      source="bettor_venue_realism.classify", evidence=ev)
    if cand["settlement"].get("fixture_read") is True and \
            cand["settlement"].get("game_pk") is not None and \
            (cls is None or cls.get("verdict") == vreal.REAL):
        return _check(C_REAL, PASS,
                      "official schedule entry %s read for this fixture%s"
                      % (cand["settlement"].get("game_pk"),
                         "" if cls is None else
                         "; the venue classifies it %s" % cls["verdict"]),
                      source="fixture_metadata + bettor_venue_realism",
                      evidence=ev)
    return _check(C_REAL, UNKNOWN,
                  "no official schedule entry was read for this fixture%s"
                  % ("" if cls is None else
                     " (venue realism: %s)" % cls.get("verdict")),
                  refusal=R_NOT_REAL, dependency=DEP_EVIDENCE,
                  source="fixture_metadata", evidence=ev)


def evaluate(cand: dict, *, model: dict, params: dict | None = None,
             authority: list | None = None, catalogue_row: dict | None = None,
             fee_fn=None, policy_version: str | None = None,
             policy: str = ACTIVE_POLICY, void: dict | None = None,
             fixture_row: dict | None = None) -> dict:
    """DEREK'S VERDICT ON ONE CANDIDATE. Pure and deterministic given its
    inputs; every figure is from decision-time data on the candidate, the
    registry read in `model`, and the authority reads in `authority`.

    `policy` is the combination rule (ACTIVE_POLICY unless a replay asks for
    V1); `policy_version` the label the decision is recorded under (the
    policy's own name unless the registry supplied the parameters)."""
    from .. import bettor_funded_execution as FX
    from ..bettor_funded_model import (
        ENTRY_PAYOUT_DESCRIPTION as FM_DESCRIPTION)
    if policy not in POLICY_RULES:
        raise ValueError("unknown Derek entry policy %r" % (policy,))
    policy_version = policy_version or policy
    prm = dict(DEFAULT_PARAMS, **(params or {}))
    checks: list = []
    lane = list(cand.get("refusals") or [])
    by_check: dict = {}
    for code in lane:
        stage = _lane_stage(code)
        name = (C_DEPTH if str(code) in VENUE_CURRENCY_CODES
                else STAGE_TO_CHECK.get(stage))
        if name:
            by_check.setdefault(name, []).append(code)

    # 0 · PURPOSE
    purpose_ok = cand.get("record_purpose") == "ENTRY_DECISION"
    checks.append(_check(
        C_PURPOSE, PASS if purpose_ok else FAIL,
        "record_purpose=%s" % cand.get("record_purpose"),
        refusal=R_NOT_ENTRY, dependency=DEP_EVIDENCE,
        source="bettor_valuation_purpose"))

    # 1 · IDENTITY
    idv = {"us_market_slug": cand.get("us_market_slug"),
           "side": cand.get("side"), "payout_event": cand.get("payout_event"),
           "period": cand.get("period"),
           "period_basis": cand.get("period_basis"),
           "fixture": cand.get("fixture"),
           "participants": [cand["settlement"].get("home_team"),
                            cand["settlement"].get("away_team")],
           "official_date": cand["settlement"].get("official_date"),
           "payout_binding_ok": cand.get("payout_binding_ok"),
           "lane_refusals": by_check.get(C_IDENTITY, [])}
    missing = [k for k in ("us_market_slug", "payout_event", "period",
                           "fixture") if not cand.get(k)]
    why_not = (list(by_check.get(C_IDENTITY) or [])
               + ["missing:%s" % k for k in missing]
               + ([] if cand.get("side") in (LONG, SHORT)
                  else ["side:%r" % cand.get("side")])
               + (["payout outcome not bound"]
                  if cand.get("payout_binding_ok") is False else []))
    if why_not:
        checks.append(_check(
            C_IDENTITY, FAIL,
            "identity not established: %s" % why_not,
            refusal=R_IDENTITY, dependency=DEP_EVIDENCE,
            source="ext_pinnacle_loop.resolve_venue_identity / "
                   "bind_payout_outcome", evidence=idv))
    else:
        checks.append(_check(
            C_IDENTITY, PASS,
            "%s %s on %s, period %s (%s)" % (
                cand.get("side"), cand.get("payout_event"),
                cand.get("us_market_slug"), cand.get("period"),
                cand.get("period_basis")),
            source="ext_pinnacle_loop.resolve_venue_identity", evidence=idv))

    # 2 · REAL EVENT
    checks.append(_realism(cand, catalogue_row))

    # 3 · SETTLEMENT
    st = cand["settlement"]
    sev = {"compatibility": st.get("compatibility"),
           "overall_established": st.get("overall_established"),
           "unmet": st.get("unmet"),
           "lane_refusals": by_check.get(C_SETTLEMENT, [])}
    if st.get("compatibility") == "COMPATIBLE" and \
            st.get("overall_established") is True and \
            not by_check.get(C_SETTLEMENT):
        checks.append(_check(C_SETTLEMENT, PASS,
                             "venue and book settlement terms compared "
                             "COMPATIBLE and every rule established",
                             source="bettor_venue_settlement.attest",
                             evidence=sev))
    elif st.get("compatibility") == "INCOMPATIBLE" or \
            by_check.get(C_SETTLEMENT) or st.get("overall_established") is \
            False:
        checks.append(_check(C_SETTLEMENT, FAIL,
                             "settlement not supported: %s" % (
                                 by_check.get(C_SETTLEMENT)
                                 or st.get("unmet")
                                 or st.get("compatibility")),
                             refusal=R_SETTLEMENT, dependency=DEP_EVIDENCE,
                             source="bettor_venue_settlement.attest",
                             evidence=sev))
    else:
        checks.append(_check(C_SETTLEMENT, UNKNOWN,
                             "the settlement comparison is %s"
                             % st.get("compatibility"),
                             refusal=R_SETTLEMENT, dependency=DEP_EVIDENCE,
                             source="bettor_venue_settlement.attest",
                             evidence=sev))

    # 4 · PROBABILITY EVIDENCE (the lane's freshness rule)
    pin = dict(cand.get("pinnacle") or {})
    fr = dict(cand.get("freshness") or {})
    pev = {"pinnacle_p": pin.get("p"), "freshness": fr,
           "lane_refusals": [c for c in by_check.get(C_PROBABILITY, [])]}
    if pin.get("p") is None:
        checks.append(_check(C_PROBABILITY, FAIL,
                             "no de-vigged Pinnacle probability",
                             refusal=R_NO_PINNACLE, dependency=DEP_EVIDENCE,
                             source="bettor_pinnacle_devig", evidence=pev))
    elif fr.get("fresh") is True:
        checks.append(_check(C_PROBABILITY, PASS, fr.get("why") or "fresh",
                             source="ext_pinnacle_loop._entry_freshness",
                             evidence=pev))
    elif fr.get("fresh") is False:
        checks.append(_check(C_PROBABILITY, FAIL, fr.get("why") or "stale",
                             refusal=R_STALE, dependency=DEP_EVIDENCE,
                             source="ext_pinnacle_loop._entry_freshness",
                             evidence=pev))
    else:
        checks.append(_check(C_PROBABILITY, UNKNOWN,
                             fr.get("why") or "a clock was not measured",
                             refusal=R_FRESHNESS_UNKNOWN,
                             dependency=DEP_EVIDENCE,
                             source="ext_pinnacle_loop._entry_freshness",
                             evidence=pev))

    # 5 · BOOK CURRENCY AND EXECUTABLE DEPTH
    est = dict(cand.get("est") or {})
    cur = dict(cand.get("book_currency") or {})
    dev = {"book_currency": cur, "estimate_ok": est.get("ok"),
           "size": est.get("size"), "levels_taken": est.get("levels_taken"),
           "lane_refusals": by_check.get(C_DEPTH, [])}
    if by_check.get(C_DEPTH) or (cur.get("verdict") not in
                                 (None, "BOOK_CURRENCY_ESTABLISHED")):
        checks.append(_check(C_DEPTH, FAIL,
                             "depth or currency refused: %s"
                             % (by_check.get(C_DEPTH) or cur.get("verdict")),
                             refusal=R_NO_DEPTH, dependency=DEP_EVIDENCE,
                             source="ext_pinnacle_loop.venue_quote / "
                                    "bettor_entry_execution.estimate",
                             evidence=dev))
    elif cur.get("verdict") == "BOOK_CURRENCY_ESTABLISHED" and \
            est.get("ok") and (_f(est.get("size")) or 0) > 0:
        checks.append(_check(C_DEPTH, PASS,
                             "currency ESTABLISHED (%s); %s contracts of "
                             "walked depth" % (cur.get("mechanism"),
                                               est.get("size")),
                             source="bettor_venue_currency + "
                                    "bettor_entry_execution.estimate",
                             evidence=dev))
    else:
        checks.append(_check(C_DEPTH, UNKNOWN,
                             "no sized walk of an established book",
                             refusal=R_NO_DEPTH, dependency=DEP_EVIDENCE,
                             source="bettor_entry_execution.estimate",
                             evidence=dev))

    # 6 · TICK AND LIMIT (the connector's own pure plan)
    try:
        plan = FX.plan_from_decision(dict(cand.get("plan_input") or {},
                                          admissible=True))
    except Exception as exc:                                   # noqa: BLE001
        plan = {"ok": False, "refusal": "PLAN_RAISED:%s" % type(exc).__name__}
    tev = {k: plan.get(k) for k in ("ok", "refusal", "limit_price",
                                    "quantity", "intent", "rounded",
                                    "counted_levels")}
    if plan.get("ok"):
        checks.append(_check(C_TICK, PASS,
                             "limit %s x %s contracts, representable on the "
                             "venue grid" % (plan.get("limit_price"),
                                             plan.get("quantity")),
                             source="bettor_funded_execution."
                                    "plan_from_decision", evidence=tev))
    else:
        checks.append(_check(C_TICK, FAIL if est.get("ok") else UNKNOWN,
                             "no supported limit: %s" % plan.get("refusal"),
                             refusal=R_LIMIT, dependency=DEP_EVIDENCE,
                             source="bettor_funded_execution."
                                    "plan_from_decision", evidence=tev))
    qty = plan.get("quantity") if plan.get("ok") else None
    levels = [{"price": lv.get("price"), "qty": lv.get("qty")}
              for lv in (est.get("levels_taken") or [])
              if isinstance(lv, dict)]
    fills = walk(levels, qty) if (qty and levels) else (
        [(float(cand["executable_price"]), float(qty))]
        if qty and cand.get("executable_price") is not None else [])

    # 7 · FEES + THE ECONOMICS
    econ = economics(p_pinnacle=pin.get("p"),
                     p_model=(model.get("p") if model.get("ok") else None),
                     fills=fills, fee_fn=fee_fn,
                     at=cand.get("decided_at"), params=prm, policy=policy)
    if econ.get("ok") and econ.get("fees_ok"):
        checks.append(_check(C_FEES, PASS,
                             "$%.4f over %s fill(s), bettor_funded_book."
                             "fee_for at each exact price and quantity"
                             % (econ["fees_usd"], len(fills)),
                             source="bettor_funded_book.fee_for",
                             evidence={"fee_basis": econ.get("fee_basis")}))
    elif econ.get("ok"):
        checks.append(_check(C_FEES, FAIL,
                             "the deployed schedule would not price a fill",
                             refusal=R_FEES, dependency=DEP_ENGINEERING,
                             source="bettor_funded_book.fee_for",
                             evidence={"fee_basis": econ.get("fee_basis")}))
    else:
        checks.append(_check(C_FEES, UNKNOWN,
                             "no sized quantity to price fees on",
                             refusal=R_NO_QTY, dependency=DEP_EVIDENCE,
                             source="bettor_funded_book.fee_for"))

    # 8 · CAPACITY UNDER THE LANE'S RAILS
    risk = dict(cand.get("risk") or {})
    cev = {"permitted": risk.get("permitted"),
           "rail_headroom": risk.get("rail_headroom"),
           "qty_cap": risk.get("qty_cap"),
           "lane_refusals": by_check.get(C_CAPACITY, [])}
    if by_check.get(C_CAPACITY):
        checks.append(_check(C_CAPACITY, FAIL,
                             "the lane's rails refused: %s"
                             % by_check[C_CAPACITY], refusal=R_CAPACITY,
                             dependency=DEP_OWNER_DECISION,
                             source="bettor_entry_execution rails / risk",
                             evidence=cev))
    elif risk.get("permitted") is True:
        checks.append(_check(C_CAPACITY, PASS,
                             "the lane's risk engine permitted the sized "
                             "position within every rail",
                             source="bettor_entry_execution.verdict",
                             evidence=cev))
    else:
        checks.append(_check(C_CAPACITY, UNKNOWN,
                             "no risk verdict on a sized position",
                             refusal=R_CAPACITY, dependency=DEP_EVIDENCE,
                             source="bettor_entry_execution.verdict",
                             evidence=cev))

    # 9 · EXECUTION AUTHORITY (recorded, not Derek's to decide)
    for a in (authority or []):
        checks.append(dict(a, blocks=BLOCKS_EXECUTION))
    names = {c["check"] for c in checks}
    for name in (C_ACCOUNT, C_LIMITS, C_SUBMISSION):
        if name not in names:
            checks.append(_check(name, UNKNOWN,
                                 "not read for this decision",
                                 blocks=BLOCKS_EXECUTION,
                                 refusal="NOT_READ",
                                 dependency=DEP_ENGINEERING))

    # 10 · THE INTERNAL MODEL
    if model.get("ok"):
        checks.append(_check(C_MODEL, PASS,
                             "%s@%s approved by %s, provenance verified"
                             % (model.get("model_id"),
                                model.get("model_version"),
                                model.get("approved_by")),
                             source="bettor_funded_model.approved",
                             evidence={k: model.get(k) for k in (
                                 "model_id", "model_version", "approved_at",
                                 "provenance_verified", "uncertainty")}))
    else:
        checks.append(_check(C_MODEL, FAIL, model.get("why") or
                             model.get("refusal"),
                             refusal=model.get("refusal") or R_NO_MODEL,
                             dependency=DEP_ENGINEERING,
                             source="bettor_funded_model.approved",
                             evidence={"registry_refusal":
                                       model.get("registry_refusal")}))

    # 11 + 12 · THE COMBINATION RULE AND NET EV AFTER FEES -- one pure
    # function (`decide_entry`), whose whole output is recorded and shown.
    prob = next(c for c in checks if c["check"] == C_PROBABILITY)
    pd = decide_entry(
        policy,
        internal={"p": model.get("p") if model.get("ok") else None,
                  "qualified": bool(model.get("ok")),
                  "refusal": (None if model.get("ok")
                              else model.get("refusal") or R_NO_MODEL),
                  "why": model.get("why"),
                  "model_id": model.get("model_id"),
                  "model_version": model.get("model_version"),
                  "at": cand.get("decided_at")},
        pinnacle={"p": pin.get("p"),
                  "qualified": prob["status"] == PASS,
                  "qualification": fr.get("pinnacle_qualification"),
                  "refusal": prob["refusal"],
                  "why": prob["detail"],
                  "at": pin.get("observed_at")},
        econ=econ, params=prm, void=void,
        void_refunds_price=(None if st.get("compatibility") is None
                            else st.get("compatibility") == "COMPATIBLE"),
        policy_version=policy_version)
    pd["instrument"] = instrument_label(cand, catalogue_row=catalogue_row,
                                        fixture_row=fixture_row)
    checks.append(combination_check(pd))
    checks.append(net_ev_check(pd))

    # 13 · THE LANE'S OWN ADMISSION (its remaining gates)
    other = [c for c in lane if not any(c in v for v in by_check.values())]
    if cand.get("admissible") and not lane:
        checks.append(_check(C_LANE, PASS, "admitted by the entry gate",
                             source="bettor_entry_gate.admit"))
    else:
        first = (other or lane or ["NOT_ADMITTED"])[0]
        checks.append(_check(C_LANE, FAIL,
                             "the entry lane refused: %s" % (other or lane),
                             refusal="%s:%s" % (R_LANE, first),
                             dependency=classify_lane_code(first),
                             source="bettor_entry_gate.admit",
                             evidence={"lane_refusals": lane}))

    # ── THE VERDICT: first failing POLICY check, in the order above ───
    order = (C_PURPOSE, C_IDENTITY, C_REAL, C_SETTLEMENT, C_PROBABILITY,
             C_DEPTH, C_TICK, C_FEES, C_CAPACITY, C_MODEL, C_AGREEMENT,
             C_BLENDED, C_NET_EV, C_LANE)
    by_name = {c["check"]: c for c in checks}
    failing = [by_name[n] for n in order
               if n in by_name and by_name[n]["status"] != PASS
               and by_name[n]["blocks"] == BLOCKS_POLICY]
    verdict = ENTER if not failing else REFUSE
    refusal = None if not failing else failing[0]["refusal"]
    # THE RECORD'S RATIONALE: built only from the stored fields, with the
    # overall verdict (a non-combination check may refuse a V2 admission).
    pd["verdict"], pd["decision_refusal"] = verdict, refusal
    pd["rationale"] = rationale(pd, verdict=verdict, refusal=refusal)
    exec_blockers = [
        {"check": c["check"], "status": c["status"], "refusal": c["refusal"],
         "dependency": c["dependency"]}
        for c in checks if c["blocks"] == BLOCKS_EXECUTION
        and c["status"] != PASS]
    pin_q = fr.get("pinnacle_qualification")
    return {
        "policy_version": policy_version,
        "policy_key": POLICY_KEY,
        "policy_name": policy,
        "combination_policy": POLICY_RULES[policy],
        "policy_decision": pd,
        "params": prm, "param_units": dict(PARAM_UNITS),
        "verdict": verdict, "refusal": refusal,
        "all_refusals": [c["refusal"] for c in failing],
        "execution_authority_blockers": exec_blockers,
        "sendable_now": verdict == ENTER and not exec_blockers,
        "what_enter_means": (
            "Derek's entry policy is satisfied on decision-time evidence. The "
            "funded connector -- the one execution authority -- still enforces "
            "the account, the owner's limits and authorization, and the "
            "submission switches; `execution_authority_blockers` lists what "
            "stands in the way of a send today"),
        "checks": checks,
        "estimates": {
            "pinnacle": {
                "p": pin.get("p"), "at": pin.get("observed_at"),
                "received_at": pin.get("received_at"),
                "qualification": pin_q,
                "age_s_at_decision": fr.get("pinnacle_age_s"),
                "limit_s": fr.get("pinnacle_limit_s"),
                "uncertainty": {"overround": pin.get("overround"),
                                "devig_method": pin.get("method"),
                                "kind": "BOOKMAKER_MARGIN_NOT_AN_INTERVAL"},
                "source_version": pin.get("source_version")},
            "model": {
                "p": model.get("p") if model.get("ok") else None,
                "model_key": model.get("model_key"),
                "model_id": model.get("model_id"),
                "model_version": model.get("model_version"),
                "approved_by": model.get("approved_by"),
                "at": cand.get("decided_at"),
                "qualification": (
                    fr.get("venue_qualification") if model.get("ok")
                    else "NO_APPROVED_MODEL"),
                "qualification_basis": (
                    "the model's only live input is the venue book, so it "
                    "carries the venue arm of the same freshness rule"),
                "uncertainty": model.get("uncertainty"),
                "is": FM_DESCRIPTION,
                # WHAT THE POLICY'S USE OF THIS ESTIMATE IS (V2: one half of
                # an average with Pinnacle; not independent confirmation).
                "policy_use_is": pd["inputs_are"],
                "refusal": model.get("refusal")},
            "blended": {"p": pd["p_blended"],
                        "is": ("(internal + Pinnacle) / 2 of two qualified "
                               "estimates" if policy == POLICY_V2 else
                               "NOT_USED_BY_%s" % policy)}},
        "economics": econ,
        "features": model.get("features"),
        "feature_sha": model.get("feature_sha"),
    }


def classify_lane_code(code) -> str:
    """Who can clear a lane refusal, by stage. Unknown codes are EVIDENCE."""
    code = str(code or "")
    if code.startswith("EXPERIMENT_NOT_ARMED"):
        return DEP_ENGINEERING_CONFIGURATION
    stage = _lane_stage(code)
    if stage == "8_ECONOMICS":
        return DEP_NONE_MARKET
    if stage in ("6_SIZING",):
        return DEP_OWNER_DECISION
    if code in ("RISK_GATE_BLOCKED",):
        # STATE gates (calibration, staleness) sit here: the source's
        # calibration needs independent resolved outcomes to accumulate.
        return DEP_ELAPSED_TIME
    return DEP_EVIDENCE


# ═════════════════════════════════════════════════════════════════════════
# RECORDING
# ═════════════════════════════════════════════════════════════════════════

INSERT_SQL = """
    INSERT INTO derek_entry_decisions
        (decision_id, valuation_id, fixture, us_market_slug, side, decided_at,
         policy_version, pinnacle_p, pinnacle_at, pinnacle_qualification,
         model_p, model_version, model_at, model_qualification,
         executable_price, qty, gross_edge_pp, expected_gross_profit_usd,
         fees_usd, expected_net_profit_usd, expected_net_roi, checks, verdict,
         refusal, latency, evidence, features, feature_sha, decided_by)
    VALUES ($1,$2,$3,$4,$5,to_timestamp($6),$7,$8,
            CASE WHEN $9::float8 IS NULL THEN NULL ELSE to_timestamp($9) END,
            $10,$11,$12,
            CASE WHEN $13::float8 IS NULL THEN NULL ELSE to_timestamp($13) END,
            $14,$15::float8,$16::float8,$17::float8,$18::float8,$19::float8,
            $20::float8,$21::float8,$22::jsonb,$23,$24,$25::jsonb,
            $26::jsonb,$27::jsonb,$28,$29)
    ON CONFLICT DO NOTHING
    RETURNING decision_id
"""


def row_values(decision_id: str, cand: dict, dec: dict, *, latency: dict,
               decided_by: str, evidence: dict) -> tuple:
    """THE HEADLINE COLUMNS ARE COPIED FROM `decide_entry`'s OUTPUT (the
    policy's own probability: V2 the blended average, V1 the lower), so the
    columns, evidence.policy_decision and the verdict are one computation."""
    econ = dec.get("economics") or {}
    pd = dec.get("policy_decision") or {}
    # (the column gross_edge_pp holds the FRACTION, as it always has)
    head = {"gross_edge_pp": pd.get("gross_edge_fraction"),
            "expected_gross_profit_usd": pd.get("expected_gross_profit_usd"),
            "expected_net_profit_usd": pd.get("net_expected_profit_usd"),
            "expected_net_roi": pd.get("expected_return_on_capital")}
    est = dec["estimates"]
    return (
        decision_id, cand.get("valuation_id"), cand.get("fixture"),
        cand.get("us_market_slug"), cand.get("side"),
        float(cand.get("decided_at") or time.time()), dec["policy_version"],
        est["pinnacle"]["p"], est["pinnacle"]["at"],
        est["pinnacle"]["qualification"],
        est["model"]["p"], est["model"]["model_version"],
        (est["model"]["at"] if est["model"]["p"] is not None else None),
        est["model"]["qualification"],
        econ.get("executable_price") if econ.get("ok")
        else cand.get("executable_price"),
        econ.get("qty") if econ.get("ok") else None,
        head.get("gross_edge_pp"), head.get("expected_gross_profit_usd"),
        econ.get("fees_usd") if econ.get("ok") else None,
        head.get("expected_net_profit_usd"), head.get("expected_net_roi"),
        json.dumps(dec["checks"], default=str), dec["verdict"],
        dec["refusal"], json.dumps(latency or {}, default=str),
        json.dumps(evidence, default=str),
        (None if dec.get("features") is None
         else json.dumps(dec["features"])), dec.get("feature_sha"),
        decided_by)


async def record(conn, decision_id: str, cand: dict, dec: dict, *,
                 latency: dict, decided_by: str) -> str | None:
    """Append the decision. Returns the id when written, None when one with
    this id (or for this valuation and policy) already stands."""
    evidence = {
        "links": evidence_links(decision_id, cand),
        "estimates": dec["estimates"],
        "economics": dec.get("economics"),
        "params": dec["params"], "param_units": dec["param_units"],
        "policy_key": dec.get("policy_key"),
        "policy_name": dec.get("policy_name"),
        "combination_policy": dec["combination_policy"],
        # THE ONE COMPUTATION (decide_entry), stored whole: what the
        # workspace displays is this, never a recomputation.
        "policy_decision": dec.get("policy_decision"),
        "all_refusals": dec["all_refusals"],
        "execution_authority_blockers": dec["execution_authority_blockers"],
        "sendable_now": dec["sendable_now"],
        "what_enter_means": dec["what_enter_means"],
        "freshness": cand.get("freshness"),
        "lane_refusals": cand.get("refusals"),
        "candidate_source": cand.get("source"),
    }
    return await conn.fetchval(
        INSERT_SQL, *row_values(decision_id, cand, dec, latency=latency,
                                decided_by=decided_by, evidence=evidence))


def evidence_links(decision_id: str, cand: dict) -> list:
    out = [{"kind": "derek_entry_decisions", "id": decision_id,
            "href": "/api/command/agents/derek/decisions/%s" % decision_id}]
    if cand.get("valuation_id") is not None:
        out.append({"kind": "external_valuations",
                    "id": str(cand["valuation_id"]),
                    "href": "/api/command/agents/derek/decisions/%s"
                            "#valuation" % decision_id})
    return out


def latency_for(cand: dict, *, decided_at: float) -> dict:
    """Source-to-decision from the provider's own stamp; decision-to-send and
    send-to-ack are EMPTY here (they are read from funded intents later)."""
    pin = cand.get("pinnacle") or {}
    obs, rcv = pin.get("observed_at"), pin.get("received_at")
    return {
        "source_to_decision_s": (None if obs is None
                                 else round(float(decided_at) - obs, 3)),
        "source_to_decision_basis": ("the provider's own last_update to the "
                                     "decision instant"),
        "provider_lag_s": (None if obs is None or rcv is None
                           else round(rcv - obs, 3)),
        "our_processing_s": (None if rcv is None
                             else round(float(decided_at) - rcv, 3)),
        "decision_to_send_s": None, "send_to_ack_s": None,
        "send": {"status": "EMPTY",
                 "why": "no funded order has been sent"},
    }


# ═════════════════════════════════════════════════════════════════════════
# THE FUNDED ENTRY GATE (core wires this into `_funded_attempt`)
# ═════════════════════════════════════════════════════════════════════════

async def policy_params(conn) -> dict:
    """The ACTIVE registry version of this policy, or the code default --
    labelled either way. Never raises."""
    try:
        from . import registry as REG
        got = await REG.active_policy(conn, AGENT_ID, POLICY_KEY,
                                      default=dict(DEFAULT_PARAMS))
        got = dict(got or {})
        params = dict(DEFAULT_PARAMS,
                      **{k: v for k, v in dict(got.get("params") or {}).items()
                         if k in DEFAULT_PARAMS})
        source = got.get("source") or "REGISTRY"
        # The registry labels a fallback with version 'CODE_DEFAULT'; the
        # policy that runs is still this module's declared version, and the
        # source field says the parameters came from code, not an approval.
        # A registry-held parameter version is recorded UNDER the active
        # combination rule's name, so a V1-era parameter label can never be
        # mistaken for (or collide with) the rule that now judges.
        version = (ACTIVE_POLICY if source == "CODE_DEFAULT"
                   else "%s+%s" % (ACTIVE_POLICY,
                                   got.get("version") or "REGISTRY"))
        return {"params": params, "version": version, "source": source,
                "policy": ACTIVE_POLICY,
                "params_version": got.get("version")}
    except Exception as exc:                                   # noqa: BLE001
        return {"params": dict(DEFAULT_PARAMS), "version": ACTIVE_POLICY,
                "source": "CODE_DEFAULT", "policy": ACTIVE_POLICY,
                "registry": type(exc).__name__}


async def void_measure(conn, *, through: float | None) -> dict:
    """THE MEASURED VOID RATE V2's settlement states apply, read point in
    time (`through`, never after the decision) through the one reader that
    states it, `bettor_pair_observations.void_rate`. Never raises: a failed
    or refused read leaves the valuation conditional, and says so."""
    try:
        from .. import bettor_pair_observations as PO
        got = await PO.void_rate(conn, through=through)
        return dict(got or {})
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "refusal": "VOID_RATE_READ_FAILED",
                "error": type(exc).__name__}


async def fixture_row(conn, condition_id) -> dict | None:
    """The fixture metadata row the settlement comparison reads (teams,
    official date), for the record's instrument label. Never raises."""
    if not condition_id:
        return None
    try:
        r = await conn.fetchrow(
            "SELECT official_date, home_team, away_team, game_pk "
            "  FROM fixture_metadata WHERE condition_id = $1",
            str(condition_id))
        return None if r is None else dict(r)
    except Exception:                                          # noqa: BLE001
        return None


async def catalogue_row(conn, slug) -> dict | None:
    if not slug:
        return None
    try:
        # game_start_epoch (R30A): the venue's scheduled kickoff instant, read
        # only to check an NFL contract's date mapping (slug date == the
        # kickoff's America/New_York day); it classifies nothing else.
        r = await conn.fetchrow(
            "SELECT market_slug, event_slug, event_title, question, "
            "       sports_type, "
            "       extract(epoch FROM game_start)::float8 AS game_start_epoch"
            "  FROM us_premap WHERE market_slug = $1 "
            " ORDER BY updated_at DESC LIMIT 1", str(slug))
        return None if r is None else dict(r)
    except Exception:                                          # noqa: BLE001
        return None


def _rec_digest(rec: dict, *, now: float) -> str:
    """For a record with no valuation link: the decision instant and every
    economic input, so two different questions never share one id."""
    r = dict(rec or {})
    c = dict(r.get("contract") or {})
    key = [c.get("us_market_slug"), c.get("buy_intent"),
           str(r.get("observed_at")), str(r.get("received_at")),
           repr(r.get("probability")), repr(r.get("executable_price")),
           repr(r.get("proposed_size")), repr(float(now))]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]


async def gate_for_funded_entry(conn, rec: dict, *, now: float,
                                params: dict | None = None) -> dict:
    """DEREK'S ANSWER BEFORE ANY SUBMISSION. Deterministic, never raises.

    {'verdict': 'ENTER'|'REFUSE', 'refusal': str|None, 'decision_id': ...,
     'recorded': bool, 'policy_version': ..., 'execution_authority_blockers':
     [...], 'decision': <the full evaluation>}

    Records the decision first; a decision that cannot be recorded is REFUSED
    (DEREK_DECISION_NOT_RECORDED:<exception>), because an entry nobody can
    later explain is not one this agent makes.
    """
    try:
        pol = await policy_params(conn)
        prm = dict(pol["params"], **(params or {}))
        version = pol["version"] if params is None else (
            "%s+OVERRIDE" % pol["version"])
        cand = candidate_from_rec(rec, now=float(now))
        ap = await approved_entry_model(conn)
        model = model_estimate(ap, cand, at=float(now))
        auth = await authority_checks(conn, now=float(now))
        cat = await catalogue_row(conn, cand.get("us_market_slug"))
        void = await void_measure(conn, through=float(now))
        fxr = await fixture_row(conn, cand.get("condition_id"))
        dec = evaluate(cand, model=model, params=prm, authority=auth,
                       catalogue_row=cat, policy_version=version, void=void,
                       fixture_row=fxr)
        did = decision_id_for(valuation_id=cand.get("valuation_id"),
                              rec_digest=_rec_digest(rec, now=float(now)),
                              policy_version=version)
        lat = latency_for(cand, decided_at=float(now))
        try:
            wrote = await record(conn, did, cand, dec, latency=lat,
                                 decided_by=DECIDED_BY_GATE)
            existing = None
            if wrote is None:
                existing = await conn.fetchrow(
                    "SELECT verdict, refusal FROM derek_entry_decisions "
                    " WHERE decision_id = $1", did)
        except Exception as exc:                               # noqa: BLE001
            return {"verdict": REFUSE,
                    "refusal": "%s:%s" % (R_NOT_RECORDED,
                                          type(exc).__name__),
                    "decision_id": did, "recorded": False,
                    "policy_version": version,
                    "would_have_been": dec["verdict"],
                    "decision": dec}
        verdict, refusal = dec["verdict"], dec["refusal"]
        if existing is not None:
            # THE RECORD STANDS: a second call on the same valuation is
            # answered by what was recorded, never re-decided silently.
            verdict = existing["verdict"]
            refusal = existing["refusal"]
        await _link(conn, did, cand, verdict=verdict, refusal=refusal,
                    decided_at=float(now), dec=dec)
        return {"verdict": verdict, "refusal": refusal, "decision_id": did,
                "recorded": wrote is not None,
                "already_recorded": existing is not None,
                "policy_version": version,
                "params_source": pol.get("source"),
                "execution_authority_blockers":
                    dec["execution_authority_blockers"],
                "decision": dec}
    except Exception as exc:                                   # noqa: BLE001
        return {"verdict": REFUSE,
                "refusal": "%s:%s" % (R_RAISED, type(exc).__name__),
                "error": str(exc)[:200], "recorded": False,
                "policy_version": POLICY_VERSION}


async def _link(conn, did, cand, *, verdict, refusal, decided_at, dec) -> dict:
    """registry.link_decision, guarded: a missing registry skips the LINK,
    never the decision."""
    try:
        from . import registry as REG
    except Exception as exc:                                   # noqa: BLE001
        return {"linked": False, "why": "registry unavailable: %s"
                % type(exc).__name__}
    try:
        pd = dec.get("policy_decision") or {}
        refs = [{"kind": e["kind"], "id": e["id"], "href": e["href"]}
                for e in evidence_links(did, cand)]
        return await REG.link_decision(
            conn, agent_id=AGENT_ID, kind="ENTRY_DECISION",
            subject=str(cand.get("us_market_slug") or cand.get("fixture")),
            decided_at=decided_at, verdict=verdict,
            summary={"refusal": refusal,
                     "gross_edge_pp": pd.get("gross_edge_fraction"),
                     "p_blended": pd.get("p_blended"),
                     "expected_net_profit_usd":
                         pd.get("net_expected_profit_usd"),
                     "rationale": pd.get("rationale"),
                     "policy_name": dec.get("policy_name"),
                     "policy_version": dec.get("policy_version")},
            evidence_refs=refs, decision_ref=did)
    except Exception as exc:                                   # noqa: BLE001
        return {"linked": False, "why": type(exc).__name__}


# ═════════════════════════════════════════════════════════════════════════
# THE ENTRY MODEL'S RECORDS (called by bettor_funded_model.labelled)
# ═════════════════════════════════════════════════════════════════════════

LABEL_SQL = """
    SELECT d.decision_id, d.fixture, d.features, d.feature_sha,
           d.pinnacle_p,
           extract(epoch FROM d.decided_at) AS decided_epoch,
           v.id AS valuation_id, v.outcome, v.outcome_basis,
           extract(epoch FROM v.outcome_at) AS outcome_epoch
      FROM derek_entry_decisions d
      JOIN external_valuations v ON v.id = d.valuation_id
     WHERE d.features IS NOT NULL AND d.fixture IS NOT NULL
       AND v.record_purpose = 'ENTRY_DECISION'
       AND v.experiment_id = $1
       AND v.outcome_known AND v.outcome IN (0, 1)
       AND v.outcome_basis IS NOT NULL
"""


async def labelled_entries(conn, *, after=None, through=None,
                           outcomes_through=None, decision_ids=None) -> dict:
    """EVERY DEREK DECISION WHOSE CONTRACT HAS RESOLVED: the vector written at
    the decision, and the label from the entry valuation's own outcome join
    (1 = the event this contract pays on occurred). A void, an unjoined or an
    unestablished outcome is not a label. Same shape as
    `bettor_funded_model.labelled`."""
    from .. import bettor_external_shadow as ext
    from .. import bettor_funded_model as FM
    out: dict[str, Any] = {"version": POLICY_VERSION, "rows": [],
                           "labels": []}
    keys = ("decision_ids", "groups", "fixtures", "decided_at",
            "feature_shas", "outcome_available_at", "leg_outcomes",
            "pushes", "outcome_versions",
            # THE MARKET CONTEXT the honest evaluation scores beside the model
            # (never part of the hashed training record): the Pinnacle
            # probability recorded on the decision, and the price cohort --
            # always an executable price on an established book here.
            "pinnacle_p", "price_basis", "cohorts")
    for k in keys:
        out[k] = []
    sql, args = LABEL_SQL, [ext.EXPERIMENT_ID]
    if after is not None:
        args.append(_epoch(after))
        sql += " AND extract(epoch FROM d.decided_at) > $%d" % len(args)
    if through is not None:
        args.append(_epoch(through))
        sql += " AND extract(epoch FROM d.decided_at) <= $%d" % len(args)
    if outcomes_through is not None:
        args.append(_epoch(outcomes_through))
        sql += " AND extract(epoch FROM v.outcome_at) <= $%d" % len(args)
    if decision_ids is not None:
        args.append([str(x) for x in decision_ids])
        sql += " AND d.decision_id = ANY($%d::text[])" % len(args)
    sql += " ORDER BY d.decided_at, d.decision_id"
    try:
        got = await conn.fetch(sql, *args)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal="THE_LABELS_COULD_NOT_BE_READ",
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    for r in got:
        feats = _j(r["features"]) or {}
        out["rows"].append(feats)
        out["labels"].append(float(r["outcome"]))
        out["decision_ids"].append(r["decision_id"])
        out["groups"].append(r["decision_id"])
        out["fixtures"].append(r["fixture"])
        out["decided_at"].append(float(r["decided_epoch"]))
        out["feature_shas"].append(r["feature_sha"])
        out["outcome_available_at"].append(
            None if r["outcome_epoch"] is None else float(r["outcome_epoch"]))
        out["leg_outcomes"].append([{
            "valuation_id": int(r["valuation_id"]),
            "outcome": int(r["outcome"]),
            "outcome_basis": r["outcome_basis"],
            "read_at": (None if r["outcome_epoch"] is None
                        else round(float(r["outcome_epoch"]), 6))}])
        out["pushes"].append(False)
        out["outcome_versions"].append(None)
        out["pinnacle_p"].append(_f(r["pinnacle_p"]))
        out["price_basis"].append(FM.PRICE_BASIS_EXECUTABLE)
        out["cohorts"].append(FM.COHORT_EXECUTABLE)
    out["n_events"] = len({str(f) for f in out["fixtures"]})
    return dict(out, ok=True, refusal=None, n=len(out["labels"]),
                target=FM.TARGET_ENTRY_PAYOUT,
                label_basis=("external_valuations.outcome joined from the "
                             "venue's settlement for the held side; a void "
                             "or unjoined outcome is not a label"))


def policy_of_record(row: dict) -> str | None:
    """WHICH COMBINATION RULE JUDGED A STORED DECISION. A record written
    before V2 carries no policy_name; its combination (CONSERVATIVE_AGREEMENT)
    or its version label says V1."""
    ev = row.get("evidence")
    ev = _j(ev) if not isinstance(ev, dict) else ev
    ev = ev or {}
    pd = ev.get("policy_decision") or {}
    name = pd.get("policy_name") or ev.get("policy_name")
    if name in POLICY_RULES:
        return name
    comb = ev.get("combination_policy")
    for pol, rule in POLICY_RULES.items():
        if comb == rule:
            return pol
    ver = str(row.get("policy_version") or "")
    for pol in (POLICY_V2, POLICY_V1):
        if ver.startswith(pol):
            return pol
    return None


def describe() -> dict:
    return {"agent": AGENT_ID, "policy_key": POLICY_KEY,
            "policy_version": POLICY_VERSION,
            "active_policy": ACTIVE_POLICY,
            "retained_policies": sorted(p for p in POLICY_RULES
                                        if p != ACTIVE_POLICY),
            "policy_rules": dict(POLICY_RULES),
            "policy_use_is": policy_use_is(ACTIVE_POLICY),
            "combination_policy": COMBINATION_POLICY,
            "default_params": dict(DEFAULT_PARAMS),
            "param_units": dict(PARAM_UNITS),
            "edge_tolerance_pp": EDGE_TOLERANCE_PP,
            "pre_purchase_checks": list(PRE_PURCHASE_CHECKS),
            "sends_orders": False,
            "dependency_classes": list(DEPENDENCY_CLASSES)}
