"""DEREK_ENTRY_POLICY_V1 -- THE OWNER'S ENTRY INSTRUCTION, WITH ITS UNITS.

    "5%+ EV" is read, by default, as a FIVE-PERCENTAGE-POINT GROSS PROBABILITY
    EDGE on a $0/$1 contract:

        qualified_probability - executable_acquisition_price >= min_gross_edge_pp

    with `min_gross_edge_pp = 0.05` in PROBABILITY POINTS (0.05 = 5 pp). It is
    NOT a 5% return on capital: a 5 pp edge bought at $0.50 is a 10% gross return
    on the $0.50, and the same 5 pp bought at $0.90 is 5.6%. Both are displayed,
    separately, and only the probability-point edge is the threshold.

── THE COMBINATION POLICY: CONSERVATIVE AGREEMENT ─────────────────────────

    The de-vigged Pinnacle probability (PINNACLE_DEVIG_V1, the entry lane's own
    valuation) AND the approved internal model's probability
    (bettor_funded_model.KEY_ENTRY_PAYOUT, state APPROVED, provenance verified)
    must EACH clear the threshold at the same executable price. Nothing is
    averaged. When no approved internal model exists the candidate is refused
    NO_APPROVED_INTERNAL_MODEL -- there is no fallback to Pinnacle alone and no
    pretend model. The headline economics are computed on the LOWER of the two
    probabilities, because that is the one the policy guarantees.

── NET OF FEES ─────────────────────────────────────────────────────────────

    Expected net profit = sum over the depth walk of qty_i * (p - price_i)
    minus the deployed fee schedule charged per fill,
    `bettor_funded_book.fee_for(qty_i, price_i)` -- the function the funded book
    books fees with -- and it must be POSITIVE. A further net threshold is a
    separately named parameter, `min_net_ev_usd` (dollars, default 0), never
    folded into the gross edge.

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
POLICY_VERSION = "DEREK_ENTRY_POLICY_V1"
COMBINATION_POLICY = "CONSERVATIVE_AGREEMENT"

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
C_AGREEMENT = "conservative_agreement_min_gross_edge"
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
R_DISAGREE = "ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE"
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
        if age is None or lim is None:
            return "UNKNOWN"
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
                       "overall_established": srule.get("overall_established"),
                       "unmet": srule.get("unmet"),
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
        "period": r.get("period"),
        "period_basis": ("RECORDED_ON_THE_VALUATION_ROW"
                         if r.get("period") else None),
        "pinnacle": {"p": _f(r.get("probability")),
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
        "settlement": {"compatibility": scmp.get("compatibility"),
                       "overall_established": (not settle_unmet
                                               if scmp else None),
                       "unmet": settle_unmet,
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


def economics(*, p_pinnacle, p_model, fills, fee_fn=None, at=None,
              params=None) -> dict:
    """The owner's arithmetic, in explicit units. Pure.

    `fills` is [(price_usd_per_contract, qty_contracts)] -- the depth walk the
    order would consume. Fees are charged per fill by `fee_fn` (default
    `bettor_funded_book.fee_for`, the function the funded book books with).
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

    def _one(p):
        if p is None:
            return None
        e = gross_edge(p, vwap)
        gross = sum(q * (float(p) - px) for px, q in fills)
        net = (gross - fees) if fees_ok else None
        return {"p": float(p), "gross_edge_pp": e,
                "gross_edge_percentage_points": (None if e is None
                                                 else round(e * 100.0, 9)),
                "clears_min_gross_edge": clears(e, prm["min_gross_edge_pp"]),
                "expected_gross_profit_usd": round(gross, 9),
                "expected_gross_return_on_cost": (round(gross / cost, 9)
                                                  if cost > 0 else None),
                "expected_net_profit_usd": (None if net is None
                                            else round(net, 9)),
                "expected_net_roi": (None if net is None or cost + fees <= 0
                                     else round(net / (cost + fees), 9))}
    out["pinnacle"] = _one(p_pinnacle)
    out["model"] = _one(p_model)
    ps = [x for x in (p_pinnacle, p_model) if x is not None]
    # THE HEADLINE IS THE LOWER ESTIMATE: the one the policy guarantees.
    out["headline"] = _one(min(ps)) if ps else None
    out["headline_is"] = ("THE_LOWER_OF_THE_TWO_QUALIFIED_PROBABILITIES "
                          "(conservative agreement)")
    out["roi_denominator"] = ("acquisition cost + fees: the cash the entry "
                              "deploys")
    return dict(out, ok=True, refusal=None)


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
                 "%s (%s). Conservative agreement needs one; Pinnacle alone "
                 "is never enough" % (FM.KEY_ENTRY_PAYOUT, ap.get("refusal"))))
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
                agreement_is=FM.ENTRY_POLICY_AGREEMENT_IS,
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
             fee_fn=None, policy_version: str = POLICY_VERSION) -> dict:
    """DEREK'S VERDICT ON ONE CANDIDATE. Pure and deterministic given its
    inputs; every figure is from decision-time data on the candidate, the
    registry read in `model`, and the authority reads in `authority`."""
    from .. import bettor_funded_execution as FX
    from ..bettor_funded_model import (
        ENTRY_PAYOUT_DESCRIPTION as FM_DESCRIPTION,
        ENTRY_POLICY_AGREEMENT_IS as FM_AGREEMENT_IS)
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
                     at=cand.get("decided_at"), params=prm)
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

    # 11 · CONSERVATIVE AGREEMENT
    pe = (econ.get("pinnacle") or {}) if econ.get("ok") else {}
    me = (econ.get("model") or {}) if econ.get("ok") else {}
    agv = {"min_gross_edge_pp": prm["min_gross_edge_pp"],
           "tolerance_pp": EDGE_TOLERANCE_PP,
           "pinnacle_gross_edge_pp": pe.get("gross_edge_pp"),
           "model_gross_edge_pp": me.get("gross_edge_pp"),
           "combination": COMBINATION_POLICY}
    if not econ.get("ok") or not pe:
        checks.append(_check(C_AGREEMENT, UNKNOWN,
                             "no edge could be computed (no priced quantity "
                             "or no Pinnacle probability)", refusal=R_NO_QTY,
                             dependency=DEP_EVIDENCE, evidence=agv))
    elif not pe.get("clears_min_gross_edge"):
        checks.append(_check(C_AGREEMENT, FAIL,
                             "Pinnacle edge %.6f pp-fraction is below %.4f"
                             % (pe["gross_edge_pp"], prm["min_gross_edge_pp"]),
                             refusal=R_BELOW, dependency=DEP_NONE_MARKET,
                             evidence=agv))
    elif not me:
        checks.append(_check(C_AGREEMENT, UNKNOWN,
                             "Pinnacle clears; no approved model estimate to "
                             "agree with it", refusal=R_NO_MODEL,
                             dependency=DEP_ENGINEERING, evidence=agv))
    elif not me.get("clears_min_gross_edge"):
        checks.append(_check(C_AGREEMENT, FAIL,
                             "Pinnacle clears (%.6f) and the model does not "
                             "(%.6f)" % (pe["gross_edge_pp"],
                                         me["gross_edge_pp"]),
                             refusal=R_DISAGREE, dependency=DEP_NONE_MARKET,
                             evidence=agv))
    else:
        checks.append(_check(C_AGREEMENT, PASS,
                             "both clear: Pinnacle %.6f, model %.6f >= %.4f"
                             % (pe["gross_edge_pp"], me["gross_edge_pp"],
                                prm["min_gross_edge_pp"]), evidence=agv))

    # 12 · NET EV AFTER FEES
    head = (econ.get("headline") or {}) if econ.get("ok") else {}
    net = head.get("expected_net_profit_usd")
    nev = {"expected_net_profit_usd": net,
           "min_net_ev_usd": prm["min_net_ev_usd"],
           "fees_usd": econ.get("fees_usd")}
    if net is None:
        checks.append(_check(C_NET_EV, UNKNOWN, "net EV not computable",
                             refusal=(R_FEES if econ.get("ok") else R_NO_QTY),
                             dependency=DEP_EVIDENCE, evidence=nev))
    elif net <= 0:
        checks.append(_check(C_NET_EV, FAIL,
                             "expected net $%.6f after $%.4f fees is not "
                             "positive" % (net, econ["fees_usd"]),
                             refusal=R_NET, dependency=DEP_NONE_MARKET,
                             evidence=nev))
    elif net < float(prm["min_net_ev_usd"]):
        checks.append(_check(C_NET_EV, FAIL,
                             "expected net $%.6f is below min_net_ev_usd "
                             "$%.4f" % (net, prm["min_net_ev_usd"]),
                             refusal=R_BELOW_NET, dependency=DEP_NONE_MARKET,
                             evidence=nev))
    else:
        checks.append(_check(C_NET_EV, PASS,
                             "expected net $%.6f after $%.4f fees"
                             % (net, econ["fees_usd"]), evidence=nev))

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
             C_NET_EV, C_LANE)
    by_name = {c["check"]: c for c in checks}
    failing = [by_name[n] for n in order
               if n in by_name and by_name[n]["status"] != PASS
               and by_name[n]["blocks"] == BLOCKS_POLICY]
    verdict = ENTER if not failing else REFUSE
    refusal = None if not failing else failing[0]["refusal"]
    exec_blockers = [
        {"check": c["check"], "status": c["status"], "refusal": c["refusal"],
         "dependency": c["dependency"]}
        for c in checks if c["blocks"] == BLOCKS_EXECUTION
        and c["status"] != PASS]
    pin_q = fr.get("pinnacle_qualification")
    return {
        "policy_version": policy_version,
        "policy_key": POLICY_KEY,
        "combination_policy": COMBINATION_POLICY,
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
                "agreement_is": FM_AGREEMENT_IS,
                "refusal": model.get("refusal")}},
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
    econ = dec.get("economics") or {}
    head = (econ.get("headline") or {}) if econ.get("ok") else {}
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
        "combination_policy": dec["combination_policy"],
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
        version = (POLICY_VERSION if source == "CODE_DEFAULT"
                   else (got.get("version") or POLICY_VERSION))
        return {"params": params, "version": version, "source": source}
    except Exception as exc:                                   # noqa: BLE001
        return {"params": dict(DEFAULT_PARAMS), "version": POLICY_VERSION,
                "source": "CODE_DEFAULT",
                "registry": type(exc).__name__}


async def catalogue_row(conn, slug) -> dict | None:
    if not slug:
        return None
    try:
        r = await conn.fetchrow(
            "SELECT market_slug, event_slug, event_title, question, "
            "       sports_type FROM us_premap WHERE market_slug = $1 "
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
        dec = evaluate(cand, model=model, params=prm, authority=auth,
                       catalogue_row=cat, policy_version=version)
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
        econ = dec.get("economics") or {}
        head = (econ.get("headline") or {}) if econ.get("ok") else {}
        refs = [{"kind": e["kind"], "id": e["id"], "href": e["href"]}
                for e in evidence_links(did, cand)]
        return await REG.link_decision(
            conn, agent_id=AGENT_ID, kind="ENTRY_DECISION",
            subject=str(cand.get("us_market_slug") or cand.get("fixture")),
            decided_at=decided_at, verdict=verdict,
            summary={"refusal": refusal,
                     "gross_edge_pp": head.get("gross_edge_pp"),
                     "expected_net_profit_usd":
                         head.get("expected_net_profit_usd"),
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


def describe() -> dict:
    return {"agent": AGENT_ID, "policy_key": POLICY_KEY,
            "policy_version": POLICY_VERSION,
            "combination_policy": COMBINATION_POLICY,
            "default_params": dict(DEFAULT_PARAMS),
            "param_units": dict(PARAM_UNITS),
            "edge_tolerance_pp": EDGE_TOLERANCE_PP,
            "pre_purchase_checks": list(PRE_PURCHASE_CHECKS),
            "sends_orders": False,
            "dependency_classes": list(DEPENDENCY_CLASSES)}
