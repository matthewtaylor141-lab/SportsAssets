"""BETTOR LIVE PARITY (R30): ONE CANONICAL INTENT, TWO EXECUTION ADAPTERS.

Owner requirement 2026-10-04. Real-capital BETTOR-originated execution must be
the exact system that makes the PAPER investment decision. It must NOT mirror
filled PAPER orders. PAPER and SMALL LIVE consume the SAME immutable canonical
intent through different execution adapters:

  CANONICAL DECISION INTENT     built ONCE when the investment policy decides
                                ENTER (paper_benchmark.decide_one): strategy +
                                version, evidence snapshot ids, opportunity
                                score, Derek's verdict, Karen's review state,
                                Allie's allocation, Eddie's executable-EV
                                verdict, side, venue, contract, limit, sizing
                                basis, created_at -- and the sha256 of all of
                                it. Immutable (migration 225).
  CANONICAL MANAGEMENT INTENT   built ONCE per Xavier review of a position
                                (paper_xavier.review_group): review, position
                                and valuation ids, evidence version, the
                                recommendation, target quantity, target
                                price/limit logic, ranked alternatives,
                                freshness, reason.

  PAPER ADAPTER                 the paper ledger + simulator, unchanged in
                                method: the paper order's side, quantity,
                                limit, wire price, type and time in force are
                                READ FROM THE INTENT.
  SMALL LIVE ADAPTER            constructs the real Polymarket US order from
                                the SAME intent with the SAME venue-parameter
                                builder the live lane uses
                                (execmirror.venue_params / plan_buy /
                                plan_sell). Capital is scaled (1:scale, the
                                per-order cap, buying power); logic is not.
                                MODE IS SHADOW: nothing is ever sent from
                                here. The database admits no other mode
                                (migration 225 CHECKs); turning SMALL LIVE on
                                needs a new migration and the owner's
                                explicit approval.

THE PARITY LEDGER compares, for every live-eligible intent, the two adapters
field by field and classifies the pair:

  MATCHED                      same intent, same action, same side, same
                               limit policy, same strategy and evidence; the
                               quantities equal (scale 1)
  EXPECTED_SCALE_DIFFERENCE    identical logic; the quantity differs only by
                               the capital scale, or one side was bounded by a
                               capital rule (venue minimum after scaling, the
                               per-order cap, buying power, a paper account
                               cap)
  VENUE_EXECUTION_DIFFERENCE   identical logic; a venue fact differs (an
                               unsupported order form, committed inventory,
                               no current account state)
  INCOMPLETE_COMPARISON        (R30A, management only) every compared field
                               agrees, but some management alternative was
                               evaluated on NEITHER side (the paper book's
                               INDIRECT_HEDGE), so exact parity is NOT
                               claimed: never counted as a match, never a halt
  LOGIC_DIVERGENCE             anything else: a different intent sha, action,
                               side, limit, order form, strategy, evidence, or
                               a quantity that is NOT the scaled quantity.
                               It HALTS SMALL LIVE immediately (same
                               transaction); a named human clears it.

A PAPER fill is never a SMALL LIVE fill: the SMALL LIVE lifecycle
(REJECTED/RESTING/PARTIAL/FILLED/CANCEL_PENDING/CANCELLED) is read only from
the venue's own order record (`live_state_of`, small_live_order_events).

R30A ADDITIONS (truth / convergence):

  VALIDITY        every adapter refuses an intent after its expires_at
                  (canonical_intent.decision_expiry): the PAPER adapter before
                  it submits, the SMALL LIVE adapter before it proposes, the
                  ACTUAL sibling before it claims.
  LIVE POLICY     the SMALL LIVE adapter refuses new exposure (SHADOW_EXCLUDED
                  with the reason) when the policy row behind the intent was
                  missing or unreadable, its sha mismatches, or its version is
                  not approved FOR LIVE (live_approvals); likewise when a live
                  gate's configuration approval is absent or stale. These are
                  GOVERNANCE refusals: the would-be order is still built and
                  compared for logic parity, and the readiness gate blocks on
                  them -- they are never counted as logic divergences, and
                  never as passes.
  ALLIE / EDDIE   the parity comparison also compares Allie's final
                  allocation (scaled) and Eddie's executable estimate; a
                  capital-scale difference is EXPECTED_SCALE_DIFFERENCE,
                  anything else LOGIC_DIVERGENCE.
  ALTERNATIVES    a management pair compares the evaluated alternative set:
                  an alternative evaluated on one side but not the other is a
                  LOGIC_DIVERGENCE; one evaluated on neither (the paper book's
                  INDIRECT_HEDGE) makes the pair INCOMPLETE_COMPARISON: exact
                  parity is NOT claimed, and the readiness gate blocks on it.
  CUTOVER         one row per deployment of a release (a rollback appends
                  its own row); the forward window starts at the latest
                  deployment whose decision-logic hash changed, and the
                  readiness gate blocks when the serving build's hash has no
                  cutover recorded.
  LATENCY         per-decision stage timestamps and their distributions.
  CONVERGENCE     nothing originates new exposure outside a canonical
                  intent: `authorize_live_exposure` (the canonical intent,
                  verified, current, matching, LIVE-policy admissible, and an
                  authorization only the SMALL LIVE adapter in LIVE mode can
                  issue -- impossible in SHADOW) gates the ACTUAL lane before
                  its claim and every funded acquisition, and
                  `canonical_live_authorized` gates every venue primitive
                  that can create a BUY: pmus.submit_fok (every polymarket-us
                  lane), live_executor._submit_fok (CLOB) and
                  execmirror.Venue.place.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from . import execmirror as M
from . import canonical_intent as SLV  # the sleeve constants
from .canonical_intent import (  # noqa: F401  (re-exported)
    ACT_CANCEL_FIRST, ACT_EXIT, ACT_NONE, ACT_PROTECT, ACT_REDUCE,
    ALTERNATIVES, EVALUATED, INTENT_VERSION, LIVE_POLICY_REFUSALS,
    MGMT_INTENT_VERSION, R_EXPIRY_UNAVAILABLE, R_INTENT_EXPIRED,
    UNAVAILABLE, VENUE, _INTENT_FIELDS, _MGMT_FIELDS,
    _dec, _epoch, _norm, build_decision_intent, build_management_intent,
    canonical_json, content_sha, decision_expiry, decision_intent_id,
    evaluated_set, intent_expiry_refusal, is_named_human, not_run_set,
    live_policy_verdict, management_action, management_alternatives,
    management_intent_id, opportunity_key, policy_block, sleeve_of,
    unavailable, verify_intent)

#: what the PAPER adapter reads from the intent (canonical_intent)
paper_entry_fields = SLV.paper_entry_fields

log = logging.getLogger(__name__)

PAPER_ADAPTER_VERSION = "PAPER_ADAPTER_V2"
#: the canonical SMALL LIVE adapter -- the issuer named on a LiveAuthorization
#: (live_authorization.ISSUER; a test pins them equal)
LIVE_ADAPTER_VERSION = "SMALL_LIVE_ADAPTER_V2"
PARITY_VERSION = "LIVE_PARITY_V2"
READINESS_VERSION = "LIVE_READINESS_GATE_V2"

#: THE ONLY SMALL LIVE MODE THIS CODE KNOWS. The database CHECKs the same.
MODE_SHADOW = "SHADOW"
SMALL_LIVE_MODE = MODE_SHADOW

# parity states (migration 225)
MATCHED = "MATCHED"
SCALE = "EXPECTED_SCALE_DIFFERENCE"
VENUE_DIFF = "VENUE_EXECUTION_DIFFERENCE"
DIVERGENCE = "LOGIC_DIVERGENCE"
#: R30A section 8: every compared field agrees, but the management
#: alternative set was evaluated on NEITHER side for some alternative (the
#: paper book runs no indirect-hedge search), so exact parity is NOT claimed.
#: Not a match (readiness never counts it as one), not a divergence (both
#: sides did the same thing; nothing halts).
INCOMPLETE = "INCOMPLETE_COMPARISON"
PARITY_STATES = (MATCHED, SCALE, VENUE_DIFF, INCOMPLETE, DIVERGENCE)


# adapter states (migration 225)
P_SUBMITTED, P_REFUSED, P_NO_ORDER = ("PAPER_SUBMITTED", "PAPER_REFUSED",
                                      "PAPER_NO_ORDER")
S_PROPOSED, S_EXCLUDED, S_NO_ORDER, S_HALTED = (
    "SHADOW_PROPOSED", "SHADOW_EXCLUDED", "SHADOW_NO_ORDER", "SHADOW_HALTED")

#: live exclusions that are capital/scale bounds, not logic
CAPITAL_EXCLUSIONS = {M.BELOW_VENUE_MINIMUM, M.ABOVE_ORDER_CAP,
                      M.INSUFFICIENT_CASH, M.NO_LIVE_INVENTORY}
#: live exclusions that are venue facts, not logic. An intent that expired
#: before the SMALL LIVE adapter reached it is a TIMING fact of execution
#: (both adapters read the same expires_at; the later one found it past).
VENUE_EXCLUSIONS = {M.UNSUPPORTED_ORDER, M.INVENTORY_COMMITTED,
                    "ACCOUNT_STATE_NOT_CURRENT", R_INTENT_EXPIRED,
                    R_EXPIRY_UNAVAILABLE}
#: GOVERNANCE refusals of new live exposure (R30A sections 23 / 24): the
#: LIVE policy verdict and the live gates' configuration approvals. They are
#: neither logic nor capital: the would-be order is still built and compared
#: (so parity evidence accumulates before an approval exists), the SMALL LIVE
#: record is SHADOW_EXCLUDED with the reason, and the readiness gate blocks
#: while any row of its sample carries one.
R_GATE_APPROVAL = "LIVE_GATE_APPROVAL_ABSENT_OR_STALE"
GOVERNANCE_EXCLUSIONS = set(LIVE_POLICY_REFUSALS) | {R_GATE_APPROVAL}
#: tolerance for a scaled dollar comparison (Allie / Eddie): the scaled
#: values are computed from the same intent, so they agree to rounding
SCALED_USD_TOLERANCE = Decimal("0.000001")
#: paper-ledger refusals that are the paper account's capital caps
PAPER_CAPITAL_REFUSALS = {
    "INSUFFICIENT_AVAILABLE_PAPER_CASH", "AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_RESERVE",
    "ABOVE_THE_PER_ORDER_CAP", "ABOVE_THE_PER_MARKET_CONCENTRATION_CAP",
    "ABOVE_THE_PER_FIXTURE_CONCENTRATION_CAP", "ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS",
    "A_SALE_NEEDS_UNCOMMITTED_HELD_INVENTORY"}

#: the venue lifecycle of a SMALL LIVE order, from the venue's order record
LIVE_STATES = ("REJECTED", "RESTING", "PARTIAL", "FILLED", "CANCEL_PENDING",
               "CANCELLED")

# readiness gate (pre-declared; changing these is a new READINESS_VERSION)
MIN_DECISION_SAMPLE = 30
MIN_MANAGEMENT_SAMPLE = 30
NOT_READY = "NOT_READY"
READY = "READY_FOR_TINY_PILOT"


# ─────────────────────────── the adapters (pure) ───────────────────────

def _req(*, action, intent, side, slug, qty, limit, wire, tif, order_type,
         strategy, strategy_version, evidence_ids, sha) -> dict:
    return {"action": action, "order_intent": intent, "holding_side": side,
            "us_market_slug": slug,
            "qty": None if qty is None else str(_dec(qty)),
            "limit_price": None if limit is None else str(_dec(limit)),
            "wire_price": None if wire is None else str(_dec(wire)),
            "time_in_force": tif, "order_type": order_type,
            "strategy": strategy, "strategy_version": strategy_version,
            "evidence_ids": evidence_ids or {}, "intent_sha": sha}


def evidence_ids(intent: dict) -> dict:
    ev = intent.get("evidence") or {}
    if isinstance(ev, str):
        ev = json.loads(ev)
    return {k: ev.get(k) for k in ("valuation_id", "book_obs_id",
                                   "pinnacle_observed_at", "probability")
            if k in ev}


def _obj(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _num_s(v) -> str | None:
    """A dollar / ratio value in its normal decimal text (the comparison
    form), or None."""
    d = _dec(v)
    if d is None or d != d:
        return None
    return format(d.normalize(), "f")


#: WHAT THE ALLIE / EDDIE COMPARISON IS -- AND IS NOT (R30A section 30;
#: stated on every comparison and on the readiness report). R30A review: both
#: adapters' views are computed by allie_eddie_view from the SAME canonical
#: intent's Allie and Eddie components, so the comparison cannot produce a
#: LOGIC_DIVERGENCE from Allie's or Eddie's own logic in production. In
#: SHADOW the SMALL LIVE adapter has no capital allocator and no execution
#: estimator of its own: what it brings is LIVE CAPITAL STATE -- the live
#: per-order rail and the live account's current buying power -- which bounds
#: the scaled allocation (a capital bound: EXPECTED_SCALE_DIFFERENCE). So
#: this is a SCALE-CONSISTENCY check of the two adapters' views, never
#: evidence that an independent LIVE evaluation agreed. An independent one
#: needs a LIVE-side Allie (allocating against the live account's capital
#: and exposure) and a LIVE-side Eddie (estimating against the live order's
#: own size on the venue book); neither exists in this release.
ALLIE_EDDIE_BASIS = {
    "kind": "SCALE_CONSISTENCY_CHECK_NOT_AN_INDEPENDENT_EVALUATION",
    "both_views_from": "the one canonical intent's allie and eddie components",
    "live_side_inputs": ["live per-order rail (execmirror_control, read-only)",
                         "live buying power (latest retail snapshot)"],
    "can_detect": ("a scaling / rail inconsistency between the adapters' "
                   "views, or a view edited after the intent"),
    "cannot_detect": ("a difference in Allie's or Eddie's own logic: no "
                      "LIVE-side allocator or estimator exists in SHADOW")}


def allie_eddie_view(intent: dict, *, scale=1, live_rail_usd=None,
                     live_buying_power_usd=None) -> dict:
    """ALLIE'S FINAL ALLOCATION AND EDDIE'S EXECUTABLE ESTIMATE AS ONE ADAPTER
    SEES THEM AT ITS CAPITAL SCALE (pure; R30A section 30). Both adapters read
    the SAME intent: the PAPER adapter at scale 1, the SMALL LIVE adapter at
    1:scale, where Allie's final allocation is also bounded by LIVE CAPITAL
    STATE -- the live rail (the per-order cap the SMALL LIVE adapter
    applies) and the live account's current buying power -- and Eddie's
    executable EV is in live dollars. Scale-free facts (status,
    recommendation, edge in percentage points, fill probability, Allie's
    binding term) are carried unchanged; any of them differing between the
    adapters is logic. See ALLIE_EDDIE_BASIS for what this can and cannot
    show."""
    a, e = _obj(intent.get("allie")), _obj(intent.get("eddie"))
    s = Decimal(str(scale))
    final = _dec(a.get("final_allocatable_usd"))
    scaled = None if final is None else final / s
    rail_bound, bound_by = False, None
    for name, cap in (("LIVE_RAIL", live_rail_usd),
                      ("LIVE_BUYING_POWER", live_buying_power_usd)):
        if scaled is not None and cap is not None and \
                scaled > Decimal(str(cap)):
            scaled, rail_bound, bound_by = Decimal(str(cap)), True, name
    ev = _dec(e.get("expected_executable_ev_usd")) \
        if e.get("status") == "MEASURED" else None
    return {
        "allie": {"status": a.get("status") or "UNAVAILABLE",
                  "final_allocatable_usd": _num_s(scaled),
                  "final_binding": a.get("final_binding"),
                  "binding_constraint": a.get("binding_constraint"),
                  "live_rail_bound": rail_bound,
                  "live_capital_bound_by": bound_by, "scale": _num_s(s)},
        "eddie": {"status": e.get("status") or "UNAVAILABLE",
                  "recommendation": e.get("recommendation"),
                  "expected_executable_ev_usd": _num_s(
                      None if ev is None else ev / s),
                  "expected_net_executable_edge_pp": _num_s(e.get(
                      "expected_net_executable_edge_pp")),
                  "expected_fill_probability": _num_s(e.get(
                      "expected_fill_probability")),
                  "scale": _num_s(s)}}


def paper_entry_request(intent: dict, order: dict) -> dict:
    """The PAPER adapter's requested entry order, normalized."""
    return dict(_req(action="BUY_ENTRY", intent=order.get("intent"),
                     side=order.get("holding_side"),
                     slug=order.get("us_market_slug"), qty=order.get("qty"),
                     limit=order.get("limit_price"),
                     wire=order.get("wire_price"),
                     tif=str(order.get("time_in_force")),
                     order_type=str(order.get("order_type")),
                     strategy=order.get("strategy"),
                     strategy_version=intent["strategy_version"],
                     evidence_ids=evidence_ids(intent),
                     sha=intent["content_sha"]),
                allie_eddie=allie_eddie_view(intent, scale=1))


def _live_gates():
    from . import live_approvals as LAP
    return LAP.GATES


def governance_verdict(intent: dict, governance: dict | None) -> dict:
    """MAY LIVE TAKE NEW EXPOSURE ON THIS INTENT? (pure). `governance` is
    what the caller read from the approvals in force: {approved_policy_shas,
    approved_gates}; None reads as NOTHING approved (fail closed).

      policy  canonical_intent.live_policy_verdict on the intent's own policy
              block (row present, sha matches, version approved FOR LIVE)
      gates   the live book-currentness and settlement-compatibility gates'
              configuration approvals (live_approvals; the book gate also
              needs its 204 rule document approved)"""
    g = governance or {}
    pol = live_policy_verdict(_obj(intent.get("policy")),
                              approved_policy_shas=g.get(
                                  "approved_policy_shas") or ())
    approved = set(g.get("approved_gates") or ())
    gates = [{"gate": x, "approved": x in approved} for x in _live_gates()]
    refusals = list(pol["refusals"])
    if not all(x["approved"] for x in gates):
        refusals.append(R_GATE_APPROVAL)
    return {"admissible": not refusals, "refusals": refusals,
            "policy": pol, "gates": gates,
            "new_exposure_refused": bool(refusals)}


def live_entry_proposal(intent: dict, *, scale, buying_power,
                        max_order_usd, halted: bool = False, now=None,
                        governance: dict | None = None) -> dict:
    """THE SMALL LIVE ADAPTER, ENTRY (pure, SHADOW): the real venue order the
    SAME intent produces at capital scale. Never sent.

    R30A: an EXPIRED intent (canonical_intent.intent_expiry_refusal at `now`,
    default the wall clock) and a GOVERNANCE refusal (governance_verdict: the
    LIVE policy fails closed, a live gate approval absent or stale) make the
    proposal SHADOW_EXCLUDED with that reason. The would-be order is still
    built -- `plan_state` / `plan_exclusion` say what capital and venue facts
    alone would have done -- so the parity comparison still measures logic."""
    assert SMALL_LIVE_MODE == MODE_SHADOW
    eligible, why = M.live_eligibility({
        "strategy": intent["strategy"],
        "decision_policy_version": intent["strategy_version"], "role": "ENTRY"})
    base = {"eligibility": why, "scale": str(_dec(scale))}
    if halted:
        return dict(base, state=S_HALTED, exclusion="SMALL_LIVE_HALTED",
                    requested=None, params=None, live_qty=0)
    if not eligible:
        return dict(base, state=S_NO_ORDER,
                    exclusion=M.STRATEGY_NOT_LIVE_ELIGIBLE, requested=None,
                    params=None, live_qty=0)
    order = {"us_market_slug": intent["us_market_slug"],
             "intent": intent["order_intent"], "qty": intent["target_qty"],
             "wire_price": intent["wire_price"],
             "time_in_force": intent["time_in_force"],
             "order_type": intent["order_type"]}
    if buying_power is None:
        exact, live, _ = M.scale_qty(intent["target_qty"], scale)
        plan = M.Plan("EXCLUDED", live_qty=live, scaled_qty=exact,
                      exclusion="ACCOUNT_STATE_NOT_CURRENT",
                      detail={"why": "no current retail account snapshot"})
    else:
        plan = M.plan_buy(order, scale=scale, buying_power=buying_power,
                          max_order_usd=max_order_usd)
    live_qty = plan.live_qty if plan.live_qty else int(
        M.scale_qty(intent["target_qty"], scale)[1])
    req = _req(action="BUY_ENTRY", intent=intent["order_intent"],
               side=intent["holding_side"], slug=intent["us_market_slug"],
               qty=live_qty, limit=intent["limit_price"],
               wire=intent["wire_price"], tif=intent["time_in_force"],
               order_type=intent["order_type"], strategy=intent["strategy"],
               strategy_version=intent["strategy_version"],
               evidence_ids=evidence_ids(intent), sha=intent["content_sha"])
    req["allie_eddie"] = allie_eddie_view(
        intent, scale=scale, live_rail_usd=max_order_usd,
        live_buying_power_usd=buying_power)
    gov = governance_verdict(intent, governance)
    exp = intent_expiry_refusal(intent, now=time.time() if now is None
                                else now)
    if gov["refusals"]:
        state, excl = S_EXCLUDED, gov["refusals"][0]
    elif exp is not None:
        state, excl = S_EXCLUDED, exp
    else:
        state = S_PROPOSED if plan.state == "PLANNED" else S_EXCLUDED
        excl = plan.exclusion
    return dict(base, state=state, exclusion=excl, plan_state=plan.state,
                plan_exclusion=plan.exclusion, governance=gov,
                expiry_refusal=exp, requested=req,
                params=plan.params or None, live_qty=live_qty,
                detail={k: str(v) for k, v in (plan.detail or {}).items()})


def paper_management_request(mi: dict, taken: dict | None) -> dict:
    """The PAPER adapter's requested management order, normalized from the
    intent it executed (and what the paper book did with it)."""
    tl = mi.get("target_limit") or {}
    # a SALE the paper book actually sent is compared as sent (its own order
    # record), not as intended; protection is compared by its target
    sent = (taken or {}).get("requested") or {}
    if not sent:
        sent = {"qty": mi.get("target_qty"), "limit_price": tl.get("limit_price"),
                "wire_price": tl.get("wire_price"),
                "time_in_force": tl.get("time_in_force"),
                "order_type": tl.get("order_type"), "intent": mi.get("order_intent")}
    return dict(_req(action=mi["action"], intent=sent.get("intent"),
                     side=mi["holding_side"], slug=mi["us_market_slug"],
                     qty=sent.get("qty"), limit=sent.get("limit_price"),
                     wire=sent.get("wire_price"),
                     tif=None if sent.get("time_in_force") is None
                     else str(sent.get("time_in_force")),
                     order_type=None if sent.get("order_type") is None
                     else str(sent.get("order_type")),
                     strategy=mi.get("strategy"),
                     strategy_version=None,
                     evidence_ids={"valuation_id": mi.get("valuation_id"),
                                   "evidence_version": mi.get("evidence_version")},
                     sha=mi["content_sha"]),
                paper_taken=(taken or {}).get("taken"),
                # THE ALTERNATIVES THE PAPER REVIEW EVALUATED (the intent's
                # set: EVALUATED / UNAVAILABLE per alternative) and the ones
                # whose evaluation was NOT RUN (the comparison is incomplete)
                alternatives_evaluated=evaluated_set(mi.get("alternative_set")),
                alternatives_not_run=not_run_set(mi.get("alternative_set")))


def hypothetical_live_inventory(open_qty, scale) -> int:
    """SHADOW ONLY: the live position the scaled entry would hold. In LIVE
    mode the adapter must read the venue's inventory instead; this function
    refuses to run outside SHADOW."""
    assert SMALL_LIVE_MODE == MODE_SHADOW, "hypothetical inventory is SHADOW-only"
    return int(M.scale_qty(open_qty, scale)[1])


def live_management_proposal(mi: dict, *, scale, open_qty,
                             halted: bool = False,
                             live_alternatives: dict | None = None) -> dict:
    """THE SMALL LIVE ADAPTER, MANAGEMENT (pure, SHADOW): the venue order the
    SAME management intent produces on the scaled position. Never sent.

    THE ALTERNATIVES LIVE EVALUATED (R30A section 8). The SMALL LIVE adapter
    evaluates no management alternative of its own: it consumes the
    canonical management intent, so its evaluated set IS the intent's.
    `live_alternatives` is the seam where a LIVE-side evaluator's own set
    would enter ({alternative: EVALUATED | UNAVAILABLE}); the parity
    comparison then refuses exact parity for any alternative evaluated on one
    side but not the other (LOGIC_DIVERGENCE).

    Management actions here only ever REDUCE exposure (a sale or a resting
    protective sale of held inventory), so the LIVE policy's fail-closed rule
    for NEW exposure does not refuse them; the management policy state is
    recorded on the intent."""
    assert SMALL_LIVE_MODE == MODE_SHADOW
    alt_set = (dict(live_alternatives) if live_alternatives is not None
               else evaluated_set(mi.get("alternative_set")))
    # a LIVE-side evaluator states only statuses: what it did not evaluate
    # is not run; the intent's own set carries its run / not-run marks
    alt_not_run = (sorted(k for k, v in alt_set.items() if v != EVALUATED)
                   if live_alternatives is not None
                   else not_run_set(mi.get("alternative_set")))
    base = {"scale": str(_dec(scale)),
            "inventory_basis": "HYPOTHETICAL_SCALED_PAPER_POSITION_SHADOW_ONLY",
            "alternatives_basis": ("LIVE_SIDE_EVALUATOR" if live_alternatives
                                   is not None else
                                   "THE_CANONICAL_MANAGEMENT_INTENT_S_SET")}
    if halted:
        return dict(base, state=S_HALTED, exclusion="SMALL_LIVE_HALTED",
                    requested=None, params=None, live_qty=0)
    tl = mi.get("target_limit") or {}
    held = hypothetical_live_inventory(open_qty, scale)
    act = mi["action"]

    def req(qty):
        return dict(_req(action=act, intent=mi.get("order_intent"),
                         side=mi["holding_side"], slug=mi["us_market_slug"],
                         qty=qty, limit=tl.get("limit_price"),
                         wire=tl.get("wire_price"),
                         tif=tl.get("time_in_force"),
                         order_type=tl.get("order_type"),
                         strategy=mi.get("strategy"), strategy_version=None,
                         evidence_ids={"valuation_id": mi.get("valuation_id"),
                                       "evidence_version": mi.get(
                                           "evidence_version")},
                         sha=mi["content_sha"]),
                    alternatives_evaluated=alt_set,
                    alternatives_not_run=alt_not_run)

    if act in (ACT_NONE, ACT_CANCEL_FIRST):
        return dict(base, state=S_NO_ORDER, exclusion=None, requested=req(None),
                    params=None, live_qty=0, live_held=held)
    if act in (ACT_EXIT, ACT_REDUCE):
        order = {"us_market_slug": mi["us_market_slug"],
                 "intent": mi["order_intent"], "qty": mi["target_qty"],
                 "wire_price": tl.get("wire_price"), "time_in_force": "IOC",
                 "order_type": "MARKETABLE"}
        plan = M.plan_sell(order, scale=scale, paper_open_qty=open_qty,
                           live_held=held, live_committed=0,
                           opened_intent=None)
        q = plan.live_qty
        return dict(base, state=S_PROPOSED if plan.state == "PLANNED"
                    else S_EXCLUDED, exclusion=plan.exclusion,
                    requested=req(q), params=plan.params or None, live_qty=q,
                    live_held=held,
                    detail={k: str(v) for k, v in (plan.detail or {}).items()})
    # MAINTAIN_STANDING_PROTECTION: one resting protective sale of the held
    # quantity at the protective price
    if held < 1:
        return dict(base, state=S_EXCLUDED, exclusion=M.NO_LIVE_INVENTORY,
                    requested=req(0), params=None, live_qty=0, live_held=held)
    px = _dec(tl.get("limit_price"))
    wire = px if mi["holding_side"] == "LONG" else (Decimal(1) - px)
    order = {"us_market_slug": mi["us_market_slug"],
             "intent": mi["order_intent"], "wire_price": wire,
             "time_in_force": "GTD", "order_type": "RESTING",
             "expires_at": "AT_SETTLEMENT_OR_REVIEW"}
    return dict(base, state=S_PROPOSED, exclusion=None, requested=req(held),
                params=M.venue_params(order, held), live_qty=held,
                live_held=held)


# ─────────────────────────── parity (pure) ─────────────────────────────

LOGIC_FIELDS = ("intent_sha", "action", "order_intent", "holding_side",
                "us_market_slug", "limit_price", "wire_price", "time_in_force",
                "order_type", "strategy", "strategy_version", "evidence_ids")


def _expected_live_qty(kind: str, action: str, paper_qty, scale, *,
                       open_qty=None) -> int | None:
    if paper_qty is None:
        return None
    if kind == "DECISION" or action == ACT_PROTECT:
        return int(M.scale_qty(paper_qty, scale)[1])
    # a sale sells the same FRACTION of the (scaled) position
    held = int(M.scale_qty(open_qty if open_qty is not None else paper_qty,
                           scale)[1])
    q, po = Decimal(str(paper_qty)), Decimal(str(open_qty or 0))
    if po <= 0 or q >= po:
        return held
    return int(((q / po) * held).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _scaled_equal(paper_v, live_v, scale) -> bool | None:
    """live == paper / scale to rounding (None when either is absent)."""
    p, lv = _dec(paper_v), _dec(live_v)
    if p is None or lv is None:
        return None
    return abs(p / Decimal(str(scale)) - lv) <= SCALED_USD_TOLERANCE


def compare_allie_eddie(paper_ae: dict | None, live_ae: dict | None,
                        scale) -> tuple[dict, list]:
    """ALLIE AND EDDIE IN THE PARITY COMPARISON (pure; R30A section 30).
    Returns ({field: {...}}, [divergent fields]). A pair that predates the
    view (either side absent) is not compared and says so.

      allie_final_allocation  status and binding terms equal; live final =
                              paper final / scale, or bounded by the live
                              rail (a capital bound) -> scale difference
      eddie_estimate          status, recommendation, net edge (pp) and fill
                              probability equal; live EV = paper EV / scale"""
    if not paper_ae or not live_ae:
        return ({"allie_final_allocation": {"compared": False,
                                            "why": "VIEW_ABSENT_ON_A_SIDE"},
                 "eddie_estimate": {"compared": False,
                                    "why": "VIEW_ABSENT_ON_A_SIDE"}}, [])
    fields, div = {}, []
    pa, la = paper_ae.get("allie") or {}, live_ae.get("allie") or {}
    same_terms = all(pa.get(k) == la.get(k) for k in (
        "status", "final_binding", "binding_constraint"))
    if la.get("live_rail_bound"):
        p_scaled = (None if _dec(pa.get("final_allocatable_usd")) is None
                    else _dec(pa["final_allocatable_usd"]) / Decimal(str(scale)))
        amount_ok = (p_scaled is not None
                     and _dec(la.get("final_allocatable_usd")) is not None
                     and _dec(la["final_allocatable_usd"]) <= p_scaled
                     + SCALED_USD_TOLERANCE)
        basis = "LIVE_CAPITAL_BOUND"
    else:
        eq = _scaled_equal(pa.get("final_allocatable_usd"),
                           la.get("final_allocatable_usd"), scale)
        amount_ok = eq if eq is not None else (
            pa.get("final_allocatable_usd") is None
            and la.get("final_allocatable_usd") is None)
        basis = "PAPER_FINAL_OVER_SCALE"
    a_ok = bool(same_terms and amount_ok)
    fields["allie_final_allocation"] = {
        "compared": True, "paper": pa.get("final_allocatable_usd"),
        "live": la.get("final_allocatable_usd"), "basis": basis,
        "comparison_kind": ALLIE_EDDIE_BASIS["kind"],
        "live_capital_bound_by": la.get("live_capital_bound_by"),
        "equal": a_ok, "scale_difference": a_ok and (
            Decimal(str(scale)) != 1 or bool(la.get("live_rail_bound")))}
    if not a_ok:
        div.append("allie_final_allocation")
    pe, le = paper_ae.get("eddie") or {}, live_ae.get("eddie") or {}
    same_e = all(pe.get(k) == le.get(k) for k in (
        "status", "recommendation", "expected_net_executable_edge_pp",
        "expected_fill_probability"))
    ev = _scaled_equal(pe.get("expected_executable_ev_usd"),
                       le.get("expected_executable_ev_usd"), scale)
    ev_ok = ev if ev is not None else (
        pe.get("expected_executable_ev_usd") is None
        and le.get("expected_executable_ev_usd") is None)
    e_ok = bool(same_e and ev_ok)
    fields["eddie_estimate"] = {
        "compared": True, "paper": {k: pe.get(k) for k in (
            "recommendation", "expected_executable_ev_usd")},
        "live": {k: le.get(k) for k in (
            "recommendation", "expected_executable_ev_usd")},
        "equal": e_ok, "comparison_kind": ALLIE_EDDIE_BASIS["kind"],
        "scale_difference": e_ok and Decimal(str(scale)) != 1}
    if not e_ok:
        div.append("eddie_estimate")
    return fields, div


def compare_alternatives(paper_set: dict | None, live_set: dict | None, *,
                         paper_not_run=None, live_not_run=None
                         ) -> tuple[dict, list]:
    """THE MANAGEMENT ALTERNATIVE SETS (pure; R30A section 8). An
    alternative EVALUATED on one side but not the other is a divergence
    (`alternative:<NAME>`). Exact management parity is claimed only when no
    alternative's evaluation was NOT RUN on either side (canonical_intent.
    not_run_set): the paper book runs no indirect-hedge search, so its
    INDIRECT_HEDGE is NOT_RUN and the comparison is incomplete on it.
    An alternative UNAVAILABLE on both sides AFTER its evaluation ran (no
    standing protection to cancel, no bids) is an evaluated fact and does not
    withhold exact parity; it is listed in `evaluated_on_neither_side`.
    `*_not_run` None (a record that predates the marks) counts every
    UNAVAILABLE alternative as not run: fail closed."""
    ps, ls = dict(paper_set or {}), dict(live_set or {})
    if not ps and not ls:
        return ({"compared": False, "why": "NO_ALTERNATIVE_SET_ON_EITHER_SIDE",
                 "exact_parity_claimed": False}, [])

    def nr(given, st):
        return set(given) if given is not None else {
            k for k, v in st.items() if v != EVALUATED}
    not_run = nr(paper_not_run, ps) | nr(live_not_run, ls)
    div, neither = [], []
    per = {}
    for a in sorted(set(ALTERNATIVES) | set(ps) | set(ls)):
        pe, le = ps.get(a) == EVALUATED, ls.get(a) == EVALUATED
        per[a] = {"paper": ps.get(a), "live": ls.get(a),
                  "equal": pe == le, "not_run": a in not_run}
        if pe != le:
            div.append("alternative:%s" % a)
        elif not pe:
            neither.append(a)
    incomplete = sorted(not_run)
    return ({"compared": True, "per_alternative": per,
             "evaluated_on_neither_side": neither,
             "not_run": incomplete,
             "exact_parity_claimed": not div and not incomplete,
             "why_not_exact": (None if not incomplete else
                               "NOT_EVALUATED_ON_EITHER_SIDE:%s"
                               % ",".join(incomplete))}, div)


def compare(*, kind: str, intent: dict, paper: dict, live: dict, scale,
            open_qty=None) -> dict:
    """CLASSIFY ONE PAPER / SMALL LIVE PAIR (pure). `paper` and `live` are the
    adapter records: {state, exclusion|refusal, requested}. Returns
    {parity_state, divergence_fields, comparison}.

    R30A: a GOVERNANCE exclusion (LIVE policy / gate approval) or an expiry
    on the live record leaves the logic comparison to the would-be order
    (`plan_state` / `plan_exclusion`); the governance refusals are recorded
    on the comparison and block readiness, the expiry is a venue-timing
    difference. Allie / Eddie (decisions) and the alternative set
    (management) are compared too."""
    pr, lr = paper.get("requested") or {}, live.get("requested") or {}
    fields: dict[str, Any] = {}
    div: list[str] = []
    gov_refusals = list((live.get("governance") or {}).get("refusals") or [])
    expiry = live.get("expiry_refusal")
    if live.get("state") == S_EXCLUDED and live.get("plan_state") is not None \
            and live.get("exclusion") in (GOVERNANCE_EXCLUSIONS
                                          | {R_INTENT_EXPIRED,
                                             R_EXPIRY_UNAVAILABLE}):
        # refused for governance / expiry: the logic is compared on the
        # would-be order -- what capital and venue facts alone decided
        lstate = S_PROPOSED if live["plan_state"] == "PLANNED" else S_EXCLUDED
        lexcl = live.get("plan_exclusion")
    else:
        lstate, lexcl = live.get("state"), live.get("exclusion")
    # both adapters must have consumed THIS intent
    for side_name, rec in (("paper", pr), ("live", lr)):
        if rec and rec.get("intent_sha") not in (None, intent["content_sha"]):
            div.append("intent_sha")
    paper_order = paper.get("state") == P_SUBMITTED or (
        kind == "MANAGEMENT" and paper.get("state") in (P_SUBMITTED, P_NO_ORDER)
        and pr.get("action") not in (None, ACT_NONE))
    if lr:
        for f in LOGIC_FIELDS:
            a, b = pr.get(f), lr.get(f)
            if f == "intent_sha":
                eq = a == b == intent["content_sha"]
            else:
                eq = a == b
            fields[f] = {"paper": a, "live": b, "equal": eq}
            if not eq and f not in div:
                div.append(f)
    # quantity: live must be EXACTLY the scaled paper quantity
    exp = _expected_live_qty(kind, pr.get("action") or "", pr.get("qty"), scale,
                             open_qty=open_qty)
    lq = live.get("live_qty")
    fields["qty"] = {"paper": pr.get("qty"), "live": lq,
                     "expected_live_at_scale": exp,
                     "scale": str(_dec(scale))}
    excl = lexcl
    pref = paper.get("refusal")
    # ALLIE AND EDDIE (decisions): both adapters' views of the same intent
    ae_scale = False
    if kind == "DECISION" and lr:
        ae_fields, ae_div = compare_allie_eddie(pr.get("allie_eddie"),
                                                lr.get("allie_eddie"), scale)
        fields.update(ae_fields)
        ae_scale = any((ae_fields.get(k) or {}).get("scale_difference")
                       for k in ("allie_final_allocation", "eddie_estimate"))
        for f in ae_div:
            if f not in div:
                div.append(f)
    # THE ALTERNATIVE SETS (management)
    alt_cmp = None
    if kind == "MANAGEMENT" and lr:
        alt_cmp, alt_div = compare_alternatives(
            pr.get("alternatives_evaluated"), lr.get("alternatives_evaluated"),
            paper_not_run=pr.get("alternatives_not_run"),
            live_not_run=lr.get("alternatives_not_run"))
        for f in alt_div:
            if f not in div:
                div.append(f)
    cls = None
    if div:
        cls = DIVERGENCE
    elif lstate == S_PROPOSED and exp is not None and lq != exp:
        div.append("qty")
        cls = DIVERGENCE
    elif lstate == S_EXCLUDED:
        if excl in CAPITAL_EXCLUSIONS:
            cls = SCALE
        elif excl in VENUE_EXCLUSIONS:
            cls = VENUE_DIFF
        else:
            div.append("live_exclusion:%s" % excl)
            cls = DIVERGENCE
    elif paper.get("state") == P_REFUSED and lstate == S_PROPOSED:
        if pref in PAPER_CAPITAL_REFUSALS:
            cls = SCALE
        elif pref in VENUE_EXCLUSIONS:
            # the paper adapter found the intent expired: a timing fact
            cls = VENUE_DIFF
        else:
            div.append("paper_refusal:%s" % pref)
            cls = DIVERGENCE
    elif kind == "DECISION" and not paper_order and \
            lstate == S_PROPOSED and paper.get("state") != P_REFUSED:
        div.append("paper_order_absent")
        cls = DIVERGENCE
    if cls is None and expiry is not None:
        # identical logic; the SMALL LIVE adapter reached the intent after it
        # expired -- a venue-timing difference, recorded, never sent
        cls = VENUE_DIFF
    if cls is None:
        same_qty = (pr.get("qty") is None and lq in (0, None)) or (
            pr.get("qty") is not None and lq is not None
            and Decimal(str(pr["qty"])) == Decimal(int(lq)))
        cls = MATCHED if same_qty and not ae_scale else SCALE
    # EXACT PARITY IS NEVER CLAIMED OVER AN INCOMPLETE ALTERNATIVE SET
    # (R30A section 8). R30A review: a management pair whose INDIRECT_HEDGE
    # was evaluated on neither side was still written MATCHED (and, at the
    # production scale, a NO_ORDER / CANCEL_FIRST pair -- quantity None on
    # both sides -- was MATCHED too), which is exactly the claim the spec
    # forbids; readiness then counted it as an exact Xavier match. Any pair
    # that is not a divergence, whose alternatives comparison does not claim
    # exact parity, is INCOMPLETE_COMPARISON, and the classification it would
    # otherwise have had is kept beside the reason.
    why_not_exact = None
    state_if_complete = None
    if cls != DIVERGENCE and alt_cmp is not None and \
            not alt_cmp.get("exact_parity_claimed"):
        why_not_exact = alt_cmp.get("why_not_exact") or alt_cmp.get("why") \
            or "ALTERNATIVE_SET_NOT_COMPARED"
        state_if_complete, cls = cls, INCOMPLETE
    comp = {"fields": fields, "paper_state": paper.get("state"),
            "live_state": live.get("state"),
            "live_exclusion": live.get("exclusion"),
            "live_plan_exclusion": lexcl, "paper_refusal": pref,
            "live_governance_refusals": gov_refusals,
            "live_expiry_refusal": expiry,
            "version": PARITY_VERSION}
    if alt_cmp is not None:
        comp["alternatives"] = alt_cmp
        comp["exact_parity_claimed"] = cls in (MATCHED, SCALE, VENUE_DIFF)
    if why_not_exact is not None:
        comp["why_not_exact"] = why_not_exact
        comp["state_if_alternatives_were_complete"] = state_if_complete
    return {"parity_state": cls, "divergence_fields": div, "comparison": comp}


# ─────────────────────────── live venue lifecycle (pure) ───────────────

def live_state_of(venue_order: dict | None) -> str | None:
    """A SMALL LIVE order's lifecycle state from the VENUE'S OWN order record
    only. Never from a paper fill."""
    if not venue_order:
        return None
    st = str(venue_order.get("state") or "")
    cum = Decimal(str(venue_order.get("cumQuantity") or
                      venue_order.get("cum_qty") or 0))
    qty = Decimal(str(venue_order.get("quantity") or 0))
    if st == "ORDER_STATE_REJECTED":
        return "REJECTED"
    if st == "ORDER_STATE_FILLED" or (qty > 0 and cum >= qty):
        return "FILLED"
    if st in ("ORDER_STATE_CANCELED", "ORDER_STATE_EXPIRED",
              "ORDER_STATE_REPLACED"):
        return "CANCELLED"
    if st in ("ORDER_STATE_PENDING_CANCEL", "ORDER_STATE_CANCEL_REQUESTED"):
        return "CANCEL_PENDING"
    if cum > 0:
        return "PARTIAL"
    return "RESTING"


# ─────────────────────────── readiness (pure) ──────────────────────────

def _governance_window(inv: list, governance_now: dict | None):
    """WHICH ROWS' GOVERNANCE REFUSALS STILL SPEAK, AND WHAT IS IN FORCE NOW
    (pure; R30A review). A governance refusal is a fact about the approvals
    in force WHEN the row was recorded. Counting every refusal in the window
    made readiness unpassable for the whole window once a single row
    predated the owner's approval -- and every row before an approval
    carries one -- leaving a trivial edit to a decision-logic file (which
    restarts the window) as the only way out.

    So: with `governance_now` (readiness_report reads it), refusals count
    only in rows recorded AFTER the latest change to the approvals in force
    (`approvals_changed_at`: the newest live_approvals row or live-rule
    approval), and the CURRENT state is checked as well -- the live gates'
    configurations approved now, and the policy sha of the latest INVESTMENT
    decision in the sample approved now (a REVOKE after the last row still
    blocks). Without it (a pure caller that knows nothing about approvals),
    every row counts: fail closed."""
    if governance_now is None:
        return inv, [], {"basis": "EVERY_ROW (no approvals state supplied)"}
    since = governance_now.get("approvals_changed_at")
    counted = [r for r in inv if since is None
               or (_epoch(r.get("created_at")) or 0.0) > float(since)]
    now_refusals = []
    if not governance_now.get("gates_approved"):
        now_refusals.append(R_GATE_APPROVAL)
    approved = set(governance_now.get("approved_policy_shas") or ())
    latest = None
    for r in inv:
        if r.get("intent_kind") == "DECISION" and r.get("policy_sha"):
            latest = r
    if latest is not None and latest["policy_sha"] not in approved:
        now_refusals.append(SLV.R_POLICY_UNAPPROVED)
    return counted, now_refusals, {
        "basis": "ROWS_AFTER_THE_LATEST_APPROVALS_CHANGE_AND_THE_STATE_NOW",
        "approvals_changed_at": since,
        "rows_counted": len(counted), "rows_before_the_change": (
            len(inv) - len(counted)),
        "gates_approved_now": bool(governance_now.get("gates_approved")),
        "latest_decision_policy_sha": None if latest is None
        else latest["policy_sha"],
        "latest_decision_policy_approved_now": None if latest is None
        else latest["policy_sha"] in approved}


def readiness(rows: list, *, halted: bool, profitability: dict | None = None,
              min_decisions: int = MIN_DECISION_SAMPLE,
              min_management: int = MIN_MANAGEMENT_SAMPLE,
              governance_now: dict | None = None,
              build_logic: dict | None = None) -> dict:
    """THE LIVE READINESS GATE over the forward parity ledger (rows oldest
    first; the caller passes only rows since the cutover / last cleared halt).
    The sample is CONSECUTIVE: a LOGIC_DIVERGENCE anywhere in it fails the
    gate. Activation evidence counts the INVESTMENT sleeve only.

    R30A: `governance_now` makes governance a current-state check
    (_governance_window); an INCOMPLETE_COMPARISON is never an exact match
    and blocks (exact management parity is not claimed); `build_logic`
    ({serving_hash, cutover_hash}) blocks when the serving build's decision
    logic has no cutover recorded."""
    inv = [r for r in rows if r.get("sleeve") == SLV.INVESTMENT]
    dec = [r for r in inv if r.get("intent_kind") == "DECISION"]
    mgt = [r for r in inv if r.get("intent_kind") == "MANAGEMENT"]

    def count(rs, st):
        return sum(1 for r in rs if r.get("parity_state") == st)

    def field_rate(rs, f):
        n = sum(1 for r in rs if f in ((r.get("comparison") or {}).get(
            "fields") or {}))
        ok = sum(1 for r in rs if ((r.get("comparison") or {}).get(
            "fields") or {}).get(f, {}).get("equal") is True)
        return {"matched": ok, "compared": n,
                "rate": None if n == 0 else round(ok / n, 6)}

    def rate(num, den):
        return None if den == 0 else round(num / den, 6)

    div = count(inv, DIVERGENCE)
    incomplete = count(inv, INCOMPLETE)
    not_exact = (DIVERGENCE, INCOMPLETE)
    dec_match = sum(1 for r in dec if r.get("parity_state") not in not_exact)
    # an INCOMPLETE_COMPARISON is NOT an exact Xavier match (R30A review)
    mgt_match = sum(1 for r in mgt if r.get("parity_state") not in not_exact)
    # R30A: governance refusals (LIVE policy / gate approvals) that still
    # speak -- see _governance_window -- and what is in force now
    gov_rows_counted, gov_now, gov_basis = _governance_window(
        inv, governance_now)
    gov: dict = {}
    for r in gov_rows_counted:
        for code in ((r.get("comparison") or {}).get(
                "live_governance_refusals") or []):
            gov[code] = gov.get(code, 0) + 1
    gov_rows = sum(1 for r in gov_rows_counted if (r.get("comparison") or {})
                   .get("live_governance_refusals"))
    gov_historical: dict = {}
    counted_ids = {id(r) for r in gov_rows_counted}
    for r in inv:
        if id(r) in counted_ids:
            continue
        for code in ((r.get("comparison") or {}).get(
                "live_governance_refusals") or []):
            gov_historical[code] = gov_historical.get(code, 0) + 1
    expired = sum(1 for r in inv if (r.get("comparison") or {}).get(
        "live_expiry_refusal"))
    # R30A: management alternative sets -- exact parity is claimed only when
    # every alternative was evaluated on both sides
    alt_missing: dict = {}
    alt_not_run: dict = {}
    alt_exact = 0
    for r in mgt:
        a = (r.get("comparison") or {}).get("alternatives") or {}
        if a.get("exact_parity_claimed"):
            alt_exact += 1
        for x in a.get("evaluated_on_neither_side") or []:
            alt_missing[x] = alt_missing.get(x, 0) + 1
        for x in a.get("not_run") or []:
            alt_not_run[x] = alt_not_run.get(x, 0) + 1
    report = {
        "version": READINESS_VERSION,
        "sleeve": SLV.INVESTMENT,
        "candidate_count": len(dec),
        "management_count": len(mgt),
        "exact_decision_match": {"matched": dec_match, "of": len(dec),
                                 "rate": rate(dec_match, len(dec))},
        "exact_side_match": field_rate(dec, "holding_side"),
        "exact_limit_policy_match": {
            "limit_price": field_rate(dec, "limit_price"),
            "wire_price": field_rate(dec, "wire_price"),
            "time_in_force": field_rate(dec, "time_in_force"),
            "order_type": field_rate(dec, "order_type")},
        "xavier_management_match": {
            "matched": mgt_match, "of": len(mgt),
            "rate": rate(mgt_match, len(mgt)),
            "incomplete_comparisons": count(mgt, INCOMPLETE),
            "rule": ("exact matches only: a LOGIC_DIVERGENCE and an "
                     "INCOMPLETE_COMPARISON (an alternative evaluated on "
                     "neither side) are not matches")},
        "allie_allocation_match": field_rate(dec, "allie_final_allocation"),
        "eddie_estimate_match": field_rate(dec, "eddie_estimate"),
        "allie_eddie_basis": ALLIE_EDDIE_BASIS,
        "management_alternatives": {
            "exact_parity_claimed": alt_exact, "of": len(mgt),
            "evaluated_on_neither_side": alt_missing,
            "not_run": alt_not_run,
            "statement": ("exact management parity is NOT claimed for a "
                          "review in which some alternative's evaluation was "
                          "NOT RUN (the paper book runs no indirect-hedge "
                          "search): such a pair is INCOMPLETE_COMPARISON and "
                          "blocks the gate; an alternative evaluated on one "
                          "side only is a LOGIC_DIVERGENCE; one evaluated and "
                          "found unavailable on both sides is an evaluated "
                          "fact")},
        "live_governance_refusals": {"rows": gov_rows, "by_refusal": gov,
                                     "refused_now": gov_now,
                                     "historical_not_counted": gov_historical,
                                     "window": gov_basis},
        "live_expired_intents": expired,
        "matched": count(inv, MATCHED),
        "expected_scale_differences": count(inv, SCALE),
        "venue_only_differences": count(inv, VENUE_DIFF),
        "incomplete_comparisons": incomplete,
        "logic_divergences": div,
        "build_logic": build_logic,
        "excluded_from_activation_evidence": {
            "non_investment_rows": len(rows) - len(inv)},
        "rule": ("READY_FOR_TINY_PILOT only when: a production cutover is "
                 "recorded for the serving build's decision logic; SMALL LIVE "
                 "is not halted; >= %d consecutive "
                 "INVESTMENT decision intents and >= %d INVESTMENT "
                 "management intents compared since it (a MINIMUM PARITY "
                 "SAMPLE ONLY, not profit evidence); zero LOGIC_DIVERGENCE; "
                 "zero INCOMPLETE_COMPARISON (exact management parity is "
                 "never claimed over an alternative evaluated on neither "
                 "side); LIVE governance in force NOW (the live gates' "
                 "configurations approved, the latest decision's policy "
                 "approved) and zero intents refused for governance since "
                 "the approvals in force last changed; and, separately, the "
                 "INVESTMENT sleeve's forward "
                 "profitability verdict is SUPPORTED_BY_FORWARD_EVIDENCE "
                 "(positive forward net, positive t and bootstrap lower "
                 "bounds on per-event net, bounded drawdown, >= 30 "
                 "independent resolved events)"
                 % (min_decisions, min_management)),
    }
    blockers = []
    if build_logic is not None:
        sh, ch = build_logic.get("serving_hash"), build_logic.get(
            "cutover_hash")
        if sh is None:
            blockers.append("CURRENT_BUILD_LOGIC_HASH_UNAVAILABLE")
        elif sh != ch:
            # the serving build decides with logic no cutover names: the
            # forward window would silently span two decision logics
            blockers.append("CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER")
    if halted:
        blockers.append("SMALL_LIVE_HALTED_BY_LOGIC_DIVERGENCE")
    if div:
        blockers.append("LOGIC_DIVERGENCES_IN_SAMPLE:%d" % div)
    if incomplete:
        blockers.append("MANAGEMENT_PARITY_NOT_EXACT:INCOMPLETE_COMPARISON:%d"
                        % incomplete)
    for code in sorted(gov):
        # LIVE would have refused these intents: never activation evidence
        blockers.append("LIVE_GOVERNANCE_REFUSED:%s:%d" % (code, gov[code]))
    for code in gov_now:
        blockers.append("LIVE_GOVERNANCE_NOT_IN_FORCE:%s" % code)
    if len(dec) < min_decisions:
        blockers.append("DECISION_SAMPLE:%d_OF_%d" % (len(dec), min_decisions))
    if len(mgt) < min_management:
        blockers.append("MANAGEMENT_SAMPLE:%d_OF_%d" % (len(mgt),
                                                        min_management))
    pv = (profitability or {}).get("profitability_verdict")
    if pv != "SUPPORTED_BY_FORWARD_EVIDENCE":
        blockers.append("PROFITABILITY:%s" % (pv or "NOT_EVALUATED"))
    report["parity_gate"] = "PASS" if not [
        b for b in blockers if not b.startswith("PROFIT")
        and not b.startswith("LIVE_GOVERNANCE_")] else "FAIL"
    report["governance_gate"] = "PASS" if not gov and not gov_now else "FAIL"
    report["blockers"] = blockers
    report["recommendation"] = READY if not blockers else NOT_READY
    report["capital_activation"] = ("NOT_AUTHORIZED: SMALL LIVE stays SHADOW "
                                    "until the gate passes AND the owner gives "
                                    "explicit activation approval")
    return report


# ─────────────────────────── persistence ───────────────────────────────

def _j(v) -> str:
    return json.dumps(_norm(v), default=str)


async def _savepoint(conn, fn):
    """Run `fn(conn)` in a savepoint: a failure here never aborts the
    caller's transaction (the paper decision / review it rides on)."""
    async with conn.transaction():
        return await fn(conn)


async def control(conn) -> dict:
    r = await conn.fetchrow("SELECT * FROM small_live_control WHERE id = 1")
    return dict(r) if r else {"mode": MODE_SHADOW, "halted": True,
                              "halt_reason": "NO_CONTROL_ROW"}


async def capital_scale(conn) -> dict:
    """The capital scale and per-order cap the live lane would use (read-only
    from execmirror_control), and the latest retail buying power."""
    ctl = await conn.fetchrow(
        "SELECT scale, max_order_usd FROM execmirror_control LIMIT 1")
    snap = await conn.fetchrow(
        "SELECT balances, at FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
    bp = None
    if snap is not None:
        bal = snap["balances"]
        bal = json.loads(bal) if isinstance(bal, str) else (bal or [])
        for b in bal or []:
            if str(b.get("currency", "USD")).upper() == "USD" and \
                    b.get("buyingPower") is not None:
                bp = float(b["buyingPower"])
        age = time.time() - snap["at"].timestamp()
        if age > 180.0:
            bp = None
    return {"scale": Decimal(str((ctl or {}).get("scale") or 1000)),
            "max_order_usd": Decimal(str((ctl or {}).get("max_order_usd") or 0)),
            "buying_power": bp}


async def record_decision_intent(conn, intent: dict) -> bool:
    async def ins(c):
        return await c.fetchval(
            """INSERT INTO canonical_decision_intents (intent_id, intent_version,
                 decision_id, opportunity_id, strategy, strategy_version,
                 policy, sleeve, evidence, probability, book, risk_rails,
                 binding_constraints, evidence_refs, latency_stages,
                 opportunity_score, derek, karen, allie, eddie, venue,
                 us_market_slug, contract, holding_side, order_intent,
                 order_type, time_in_force, limit_price, wire_price, target_qty,
                 sizing_basis, created_at, expires_at, expiry, content_sha)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10::jsonb,
                 $11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,
                 $16::jsonb,$17::jsonb,$18::jsonb,$19::jsonb,$20::jsonb,$21,
                 $22,$23::jsonb,$24,$25,$26,$27,$28,$29,$30,$31::jsonb,
                 to_timestamp($32),to_timestamp($33),$34::jsonb,$35)
               ON CONFLICT (decision_id) DO NOTHING RETURNING intent_id""",
            intent["intent_id"], intent["intent_version"], intent["decision_id"],
            intent["opportunity_id"], intent["strategy"],
            intent["strategy_version"], _j(intent["policy"]),
            intent["sleeve"], _j(intent["evidence"]),
            _j(intent["probability"]), _j(intent["book"]),
            _j(intent["risk_rails"]), _j(intent["binding_constraints"]),
            _j(intent["evidence_refs"]), _j(intent["latency_stages"]),
            _j(intent["opportunity_score"]),
            _j(intent["derek"]), _j(intent["karen"]), _j(intent["allie"]),
            _j(intent["eddie"]), intent["venue"], intent["us_market_slug"],
            _j(intent["contract"]), intent["holding_side"],
            intent["order_intent"], intent["order_type"],
            intent["time_in_force"], intent["limit_price"],
            intent["wire_price"], intent["target_qty"],
            _j(intent["sizing_basis"]), intent["created_at"],
            intent["expires_at"], _j(intent["expiry"]),
            intent["content_sha"])
    return bool(await _savepoint(conn, ins))


async def record_management_intent(conn, mi: dict) -> bool:
    async def ins(c):
        return await c.fetchval(
            """INSERT INTO canonical_management_intents (intent_id,
                 intent_version, review_id, group_id, position_key, strategy,
                 sleeve, valuation_id, evidence_version, evidence_state,
                 recommendation, mechanical_selection, action, us_market_slug,
                 holding_side, order_intent, target_qty, target_limit,
                 alternatives, alternative_set, chosen, chosen_why, policy,
                 freshness, reason, created_at, content_sha)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                 $17,$18::jsonb,$19::jsonb,$20::jsonb,$21,$22::jsonb,
                 $23::jsonb,$24::jsonb,$25::jsonb,to_timestamp($26),$27)
               ON CONFLICT (review_id) DO NOTHING RETURNING intent_id""",
            mi["intent_id"], mi["intent_version"], mi["review_id"],
            mi["group_id"], mi["position_key"], mi["strategy"], mi["sleeve"],
            mi["valuation_id"], mi["evidence_version"], mi["evidence_state"],
            mi["recommendation"], mi["mechanical_selection"], mi["action"],
            mi["us_market_slug"], mi["holding_side"], mi["order_intent"],
            mi["target_qty"], _j(mi["target_limit"]), _j(mi["alternatives"]),
            _j(mi["alternative_set"]), mi["chosen"], _j(mi["chosen_why"]),
            _j(mi["policy"]), _j(mi["freshness"]), _j(mi["reason"]),
            mi["created_at"], mi["content_sha"])
    return bool(await _savepoint(conn, ins))


def _exec_id(intent_id: str, adapter: str) -> str:
    return "cie_" + hashlib.sha256(("%s:%s" % (intent_id, adapter)).encode()
                                   ).hexdigest()[:24]


async def _record_execution(c, *, kind, intent, adapter, mode, version, state,
                            exclusion, requested, scale, params, refs) -> str:
    eid = _exec_id(intent["intent_id"], adapter)
    await c.execute(
        """INSERT INTO canonical_intent_executions (execution_id, intent_kind,
             intent_id, intent_sha, adapter, mode, adapter_version, state,
             exclusion, requested, capital_scale, venue_params, refs)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12::jsonb,
                   $13::jsonb)
           ON CONFLICT (intent_id, adapter) DO NOTHING""",
        eid, kind, intent["intent_id"], intent["content_sha"], adapter, mode,
        version, state, exclusion, _j(requested or {}), Decimal(str(scale)),
        None if params is None else _j(params), _j(refs or {}))
    return eid


async def _halt(c, *, parity_id: str, reason: str, detail: dict) -> None:
    await c.execute(
        """UPDATE small_live_control SET halted = true, halted_at = now(),
             halt_reason = $1, halt_parity_id = $2, cleared_by = NULL,
             cleared_at = NULL WHERE id = 1 AND NOT halted""",
        reason, parity_id)
    await c.execute(
        """INSERT INTO small_live_control_events (action, actor, reason,
             parity_id, detail) VALUES ('HALT', 'SYSTEM_PARITY_LEDGER', $1, $2,
             $3::jsonb)""", reason, parity_id, _j(detail))


async def _record_parity(c, *, kind, intent, paper_eid, live_eid, scale,
                         result) -> str:
    pid = "lpl_" + hashlib.sha256(intent["intent_id"].encode()).hexdigest()[:24]
    got = await c.fetchval(
        """INSERT INTO live_parity_ledger (parity_id, parity_version,
             intent_kind, intent_id, intent_sha, strategy, sleeve,
             paper_execution_id, live_execution_id, capital_scale,
             parity_state, divergence_fields, comparison)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
           ON CONFLICT (intent_id) DO NOTHING RETURNING parity_id""",
        pid, PARITY_VERSION, kind, intent["intent_id"], intent["content_sha"],
        intent.get("strategy"), intent.get("sleeve") or SLV.UNCLASSIFIED,
        paper_eid, live_eid, Decimal(str(scale)), result["parity_state"],
        result["divergence_fields"], _j(result["comparison"]))
    if got and result["parity_state"] == DIVERGENCE:
        await _halt(c, parity_id=pid, reason="LOGIC_DIVERGENCE",
                    detail={"intent_id": intent["intent_id"],
                            "kind": kind,
                            "fields": result["divergence_fields"]})
    return pid


async def governance_in_force(conn) -> dict:
    """THE APPROVALS IN FORCE NOW, for governance_verdict (read-only, fail
    closed): the policy shas approved for LIVE, and the live gates whose
    configuration approval matches this build (the book gate also needs its
    204 rule document approved: live_rule_artifacts intersects both)."""
    from . import live_approvals as LAP
    from . import live_rule_artifacts as LRA
    pol = await LAP.approved_policy_shas(conn)
    book = await LRA.approved_live_book_rules(conn)
    settle = await LAP.approved_settlement_gates(conn)
    gates = frozenset(g for g in book if g == LAP.GATE_BOOK) | settle
    return {"approved_policy_shas": pol, "approved_gates": gates,
            "approvals_changed_at": await approvals_changed_at(conn)}


async def approvals_changed_at(conn) -> float | None:
    """WHEN THE APPROVALS IN FORCE LAST CHANGED (epoch seconds), or None when
    no approval has ever been recorded: the newest owner LIVE approval /
    revocation (live_approvals.recorded_at, the database clock) and the
    newest live-rule document status change (live_rule_artifacts). The
    readiness gate counts governance refusals only in rows recorded after
    it (_governance_window). Read-only; never raises (an unreadable source
    contributes nothing, which can only keep older refusals counted)."""
    out = None
    for sql in ("SELECT extract(epoch FROM max(recorded_at))::float8 "
                "  FROM live_approvals",
                "SELECT extract(epoch FROM max(greatest(status_changed_at, "
                "       owner_approved_at)))::float8 FROM live_rule_artifacts"):
        try:
            async with conn.transaction():                    # a savepoint
                v = await conn.fetchval(sql)
        except Exception:                                     # noqa: BLE001
            v = None
        if v is not None and (out is None or float(v) > out):
            out = float(v)
    return out


async def entry_adapters(conn, intent: dict, *, paper_order: dict,
                         paper_result: dict, now=None,
                         stages: dict | None = None) -> dict:
    """Record what BOTH adapters did with one canonical decision intent and
    the parity between them. The PAPER adapter already ran (the caller
    submitted `paper_order`, built from `paper_entry_fields(intent)`); the
    SMALL LIVE adapter constructs its SHADOW proposal here, at `now` (the
    caller's decision clock; default the wall clock). `stages` are the
    latency chain's adapter-side stamps (intent recorded, paper submit),
    recorded on the PAPER execution row. Never raises into the caller."""
    async def go(c):
        ctl = await control(c)
        cap = await capital_scale(c)
        if paper_result.get("ok"):
            pstate, pref = P_SUBMITTED, None
        else:
            pstate, pref = P_REFUSED, paper_result.get("refusal")
        preq = paper_entry_request(intent, paper_order)
        peid = await _record_execution(
            c, kind="DECISION", intent=intent, adapter="PAPER",
            mode="SIMULATED", version=PAPER_ADAPTER_VERSION, state=pstate,
            exclusion=pref, requested=preq, scale=1, params=None,
            refs={"order_id": (paper_result.get("order") or {}).get("order_id"),
                  "decision_id": intent["decision_id"],
                  "stages": dict(stages or {})})
        gov = await governance_in_force(c)
        live = live_entry_proposal(intent, scale=cap["scale"],
                                   buying_power=cap["buying_power"],
                                   max_order_usd=cap["max_order_usd"],
                                   halted=bool(ctl.get("halted")),
                                   now=now, governance=gov)
        if live["state"] == S_NO_ORDER and \
                live["exclusion"] == M.STRATEGY_NOT_LIVE_ELIGIBLE:
            # out of live scope by policy (TRAINING / BENCHMARK / unpromoted
            # versions): the paper record stands alone, no parity row
            return {"paper": pstate, "live": "NOT_LIVE_ELIGIBLE"}
        leid = await _record_execution(
            c, kind="DECISION", intent=intent, adapter="SMALL_LIVE",
            mode=MODE_SHADOW, version=LIVE_ADAPTER_VERSION,
            state=live["state"], exclusion=live["exclusion"],
            requested=live["requested"], scale=cap["scale"],
            params=live["params"],
            refs={"eligibility": live.get("eligibility"),
                  "detail": live.get("detail"),
                  "max_order_usd": str(cap["max_order_usd"]),
                  "buying_power_current": cap["buying_power"] is not None,
                  "governance": live.get("governance"),
                  "expiry_refusal": live.get("expiry_refusal"),
                  "plan_state": live.get("plan_state"),
                  "plan_exclusion": live.get("plan_exclusion"),
                  "new_exposure_refused": live["state"] != S_PROPOSED})
        if live["state"] == S_HALTED:
            return {"paper": pstate, "live": S_HALTED}
        res = compare(kind="DECISION", intent=intent,
                      paper={"state": pstate, "refusal": pref,
                             "requested": preq},
                      live=live, scale=cap["scale"])
        pid = await _record_parity(c, kind="DECISION", intent=intent,
                                   paper_eid=peid, live_eid=leid,
                                   scale=cap["scale"], result=res)
        return {"paper": pstate, "live": live["state"],
                "parity": res["parity_state"], "parity_id": pid}
    try:
        return await _savepoint(conn, go)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("live parity (entry) not recorded for %s",
                    intent.get("intent_id"), exc_info=True)
        return {"error": type(exc).__name__}


async def management_adapters(conn, mi: dict, *, taken: dict,
                              open_qty) -> dict:
    """Record both adapters for one canonical management intent + parity."""
    async def go(c):
        ctl = await control(c)
        cap = await capital_scale(c)
        preq = paper_management_request(mi, taken)
        t = (taken or {}).get("taken") or "NONE"
        if t == "NONE" or mi["action"] == ACT_NONE:
            pstate, pref = P_NO_ORDER, (taken or {}).get("why")
        elif (taken or {}).get("ok") is False:
            pstate, pref = P_REFUSED, (taken or {}).get("refusal")
        else:
            pstate, pref = P_SUBMITTED if t.startswith("SUBMIT") else P_NO_ORDER, None
        peid = await _record_execution(
            c, kind="MANAGEMENT", intent=mi, adapter="PAPER",
            mode="SIMULATED", version=PAPER_ADAPTER_VERSION, state=pstate,
            exclusion=pref, requested=preq, scale=1, params=None,
            refs={"taken": t, "order_id": (taken or {}).get("order_id"),
                  "review_id": mi["review_id"]})
        eligible, _ = M.live_eligibility({"strategy": mi.get("strategy"),
                                          "role": "EXIT"})
        if not eligible:
            return {"paper": pstate, "live": "NOT_LIVE_ELIGIBLE"}
        live = live_management_proposal(mi, scale=cap["scale"],
                                        open_qty=open_qty,
                                        halted=bool(ctl.get("halted")))
        leid = await _record_execution(
            c, kind="MANAGEMENT", intent=mi, adapter="SMALL_LIVE",
            mode=MODE_SHADOW, version=LIVE_ADAPTER_VERSION,
            state=live["state"], exclusion=live["exclusion"],
            requested=live["requested"], scale=cap["scale"],
            params=live["params"],
            refs={"inventory_basis": live.get("inventory_basis"),
                  "live_held": live.get("live_held"),
                  "detail": live.get("detail"),
                  "alternatives_basis": live.get("alternatives_basis"),
                  "new_exposure": False,
                  "management_policy": _obj(mi.get("policy"))})
        if live["state"] == S_HALTED:
            return {"paper": pstate, "live": S_HALTED}
        res = compare(kind="MANAGEMENT", intent=mi,
                      paper={"state": pstate, "refusal": pref,
                             "requested": preq},
                      live=live, scale=cap["scale"], open_qty=open_qty)
        pid = await _record_parity(c, kind="MANAGEMENT", intent=mi,
                                   paper_eid=peid, live_eid=leid,
                                   scale=cap["scale"], result=res)
        return {"paper": pstate, "live": live["state"],
                "parity": res["parity_state"], "parity_id": pid}
    try:
        return await _savepoint(conn, go)
    except Exception as exc:                                  # noqa: BLE001
        log.warning("live parity (management) not recorded for %s",
                    mi.get("intent_id"), exc_info=True)
        return {"error": type(exc).__name__}


async def clear_halt(conn, *, actor: str, reason: str) -> dict:
    """A named human clears a LOGIC_DIVERGENCE halt (the database refuses an
    agent or system actor). The divergence itself stays in the ledger."""
    async with conn.transaction():
        await conn.execute(
            """UPDATE small_live_control SET halted = false, cleared_by = $1,
                 cleared_at = clock_timestamp() WHERE id = 1 AND halted""",
            actor)
        await conn.execute(
            """INSERT INTO small_live_control_events (action, actor, reason)
               VALUES ('CLEAR_HALT', $1, $2)""", actor, reason)
    return await control(conn)


async def governance_now(conn) -> dict:
    """The approvals state the readiness gate checks NOW (read-only, fail
    closed): every live gate's configuration approved, the policy shas
    approved for LIVE, and when the approvals last changed."""
    try:
        gin = await governance_in_force(conn)
    except Exception as exc:                                  # noqa: BLE001
        log.debug("approvals unreadable: %s", type(exc).__name__)
        gin = {}
    gates = set(gin.get("approved_gates") or ())
    return {"gates_approved": all(g in gates for g in _live_gates()),
            "approved_gates": sorted(gates),
            "approved_policy_shas": frozenset(
                gin.get("approved_policy_shas") or ()),
            "approvals_changed_at": gin.get("approvals_changed_at")}


def serving_build_logic(cutover: dict | None, *, logic: dict | None = None
                        ) -> dict:
    """decision_logic.build_logic_check for the effective cutover, with this
    module's decision_logic_hash (the one cutover_checks records)."""
    from .decision_logic import build_logic_check
    return build_logic_check(
        None if cutover is None else cutover.get("decision_logic_hash"),
        logic=logic if logic is not None else decision_logic_hash())


async def readiness_report(conn, *, since: float | None = None,
                           profitability: dict | None = None,
                           logic: dict | None = None) -> dict:
    """The gate over the ledger since `since` (never before the EFFECTIVE
    production cutover -- the latest deployment whose decision-logic hash
    changed -- nor before the last cleared halt). R30A: governance is
    checked against the approvals in force now, and the serving build's
    decision-logic hash must be the effective cutover's."""
    ctl = await control(conn)
    cut = await production_cutover(conn)
    latest = await latest_release_cutover(conn)
    cut_at = None if cut is None else cut["cutover_at"].timestamp()
    gnow = await governance_now(conn)
    build = serving_build_logic(cut, logic=logic)
    if since is None:
        since = cut_at
    if since is not None and cut_at is not None and since < cut_at:
        since = cut_at                       # never before the cutover
    if ctl.get("cleared_at") is not None and (
            since is None or ctl["cleared_at"].timestamp() > since):
        since = ctl["cleared_at"].timestamp()
    if cut is None:
        # no production cutover: there is no forward sample yet
        out = readiness([], halted=bool(ctl.get("halted")),
                        profitability=profitability, governance_now=gnow)
        out["blockers"].insert(0, "NO_PRODUCTION_CUTOVER_RECORDED")
        out["recommendation"] = NOT_READY
        out["since"] = None
        out["cutover"] = None
        out["build_logic"] = build
        out["control"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                          for k, v in ctl.items()}
        return out
    rows = await conn.fetch(
        """SELECT l.intent_kind, l.sleeve, l.parity_state, l.divergence_fields,
                  l.comparison, l.created_at,
                  d.policy->>'policy_sha' AS policy_sha
             FROM live_parity_ledger l
             LEFT JOIN canonical_decision_intents d
               ON d.intent_id = l.intent_id
            WHERE ($1::double precision IS NULL
                   OR l.created_at >= to_timestamp($1))
            ORDER BY l.created_at, l.parity_id""", since)
    recs = []
    for r in rows:
        d = dict(r)
        if isinstance(d["comparison"], str):
            d["comparison"] = json.loads(d["comparison"])
        recs.append(d)
    out = readiness(recs, halted=bool(ctl.get("halted")),
                    profitability=profitability, governance_now=gnow,
                    build_logic=build)
    out["since"] = since
    out["cutover"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                      for k, v in cut.items() if k != "evidence"}
    out["latest_release_cutover"] = None if latest is None else {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in latest.items() if k != "evidence"}
    out["cutover_rule"] = (
        "the forward window starts at the latest deployment whose "
        "decision_logic_hash differs from its predecessor's: a release that "
        "does not change decision logic does not restart the sample, a "
        "rollback to an earlier logic appends a row and restarts it")
    first = next((r for r in recs if r.get("sleeve") == SLV.INVESTMENT
                  and r.get("intent_kind") == "DECISION"), None)
    out["observation_1"] = (None if first is None
                            else first["created_at"].isoformat())
    out["observation_rule"] = ("observation #1 is the first INVESTMENT "
                               "decision intent compared after the "
                               "production cutover")
    out["control"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                      for k, v in ctl.items()}
    return out


# ─────────────────────────── the decision hook ─────────────────────────

async def _live_rails(conn) -> dict:
    """The live rails in force, read-only (execmirror_control): the live
    lane's per-order cap and its capital scale. UNAVAILABLE with the reason
    when unreadable -- never a manufactured value."""
    try:
        async with conn.transaction():                        # a savepoint
            ctl = await conn.fetchrow(
                "SELECT scale, max_order_usd FROM execmirror_control LIMIT 1")
    except Exception as exc:                                  # noqa: BLE001
        return unavailable("LIVE_RAILS_UNREADABLE:%s" % type(exc).__name__)
    if ctl is None:
        return unavailable("NO_EXECMIRROR_CONTROL_ROW")
    scale = None if ctl["scale"] is None else Decimal(str(ctl["scale"]))
    cap = None if ctl["max_order_usd"] is None else Decimal(
        str(ctl["max_order_usd"]))
    return {"status": "MEASURED", "live_max_order_usd": cap,
            "live_scale": scale,
            "live_rail_paper_equivalent_usd": (
                None if cap is None or scale is None else cap * scale),
            "read_from": "execmirror_control (read-only)"}


async def canonical_decision(conn, *, did, strategy, version, cand, side, sized,
                           ent, obs, md, econ, p, best_edge, verdict, refusals,
                           policy_decision, at, label, book_age, cfg, params,
                           pin, book_max_age=None, book_source=None,
                           clock=None) -> dict | None:
    """THE DECISION HOOK (decision_hooks.CANONICAL_DECISION): BUILD AND
    RECORD THE ONE CANONICAL DECISION INTENT of an ENTER
    decision (live_parity.build_decision_intent), with the agent components
    computed at the decision instant (canonical_components). Returns the
    intent, or None when it could not be built or recorded (the paper
    sibling then proceeds exactly as before, and no live proposal exists).

    R30A: the intent also carries the opportunity id, the policy block (the
    parameter version, its row sha, policy_sha; a PAPER fallback labelled),
    the probability's source / version / stamps / age, the book observation
    and its age against the entry rule (`book_max_age`), the risk rails in
    force, Allie's binding constraints, evidence references, the latency
    chain's decision-side stages, and the validity window (expires_at)."""
    try:
        e = econ or {}
        cost = e.get("acquisition_cost_usd")
        if cost is None and sized.get("qty") and sized.get("wire") is not None:
            cost = float(sized["qty"]) * float(sized["wire"])
        decision = {
            "decision_id": did, "candidate_id": did, "capacity_id": None,
            "decided_at": at, "strategy": strategy, "verdict": verdict,
            "league": cand.get("league") or cand.get("sport_family"),
            "sport": cand.get("sport_family"), "fixture": cand.get("fixture"),
            "us_market_slug": cand.get("us_market_slug"),
            "holding_side": side, "proposed_qty": sized.get("qty"),
            "limit_price": sized.get("limit"), "p_pinnacle": p,
            "p_blended": None, "p_internal": None,
            "economics": {"acquisition": e},
            "executable_opportunity_dollars": e.get("expected_net_profit_usd"),
            "executable_capacity_usd": cost,
            "capital_required_usd": (None if cost is None else
                                     float(cost) + float(e.get("fees_usd") or 0)),
            "depth_within_limit": sized.get("depth_within_limit"),
            "per_order_cap_usd": (cfg.get("risk") or {}).get(
                "per_order_cap_usd"),
            "event_start_at": cand.get("event_start_at")
            or cand.get("game_start"),
            "status": "MEASURED"}
        book_row = None if obs is None else {
            "obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
            "bids": (md or {}).get("bids") or [],
            "offers": (md or {}).get("offers") or []}
        from . import canonical_components as CC
        comps = await CC.at_decision(conn, decision=decision,
                                     book_row=book_row, cost_usd=cost, p=p,
                                     wire=sized.get("wire"), now=at,
                                     contract=cand)
        pinnacle = cand.get("pinnacle") or {}
        pin = pin or {}
        prm = params if isinstance(params, dict) else None
        policy = policy_block(strategy=strategy, strategy_version=version,
                              params=prm)
        p_obs = pin.get("at") if pin.get("at") is not None else pinnacle.get(
            "observed_at")
        probability = {
            "status": "MEASURED" if p is not None else "UNAVAILABLE",
            "value": p,
            "source": pin.get("provider") or pinnacle.get("provider"),
            "source_version": pin.get("source_version")
            or pinnacle.get("source_version"),
            "method": pin.get("method") or pinnacle.get("method"),
            "observed_at": p_obs,
            "received_at": pin.get("received_at")
            or pinnacle.get("received_at"),
            "age_at_decision_s": pin.get("age_s"),
            "limit_s": pin.get("limit_s"),
            "qualified": pin.get("qualified"),
            "authority": pin.get("probability_authority"),
            "valuation_id": cand.get("valuation_id"),
            "valuation_decided_at": cand.get("decided_at")}
        book = {"status": "MEASURED" if obs is not None else "UNAVAILABLE",
                "obs_id": None if obs is None else obs["obs_id"],
                "observed_at": None if obs is None
                else float(obs["observed_at"]),
                "observed_at_is": "OUR_RECEIPT_INSTANT",
                "age_at_decision_s": book_age,
                "max_age_s": book_max_age, "source": book_source}
        risk = cfg.get("risk") or {}
        rails = await _live_rails(conn)
        risk_rails = {
            "paper_per_order_cap_usd": risk.get("per_order_cap_usd"),
            "paper_target_order_usd": ent.get("target_order_usd"),
            "paper_risk": {k: risk.get(k) for k in sorted(risk)},
            "live": rails,
            "per_order_cap_usd": risk.get("per_order_cap_usd"),
            "scale": rails.get("live_scale"),
            "basis": ("the rails the paper account applied to this order and "
                      "the live lane's per-order cap and scale the SMALL LIVE "
                      "adapter applies; none is changed here")}
        allie = comps["allie"] or {}
        eddie = comps["eddie"] or {}
        refs = [x for x in (
            {"kind": "paper_decisions", "id": did},
            {"kind": "valuation", "id": cand.get("valuation_id")}
            if cand.get("valuation_id") is not None else None,
            {"kind": "paper_book_observations", "id": obs["obs_id"]}
            if obs is not None else None,
            {"kind": "policy_parameters_version",
             "id": (prm or {}).get("version_id")}
            if (prm or {}).get("version_id") else None,
            {"kind": "policy_parameters_activation",
             "id": (prm or {}).get("activation_id")}
            if (prm or {}).get("activation_id") else None,
            {"kind": "eddie_estimate", "id": eddie.get("estimate_id")}
            if eddie.get("estimate_id") else None) if x]
        def _us(v):
            # every stage stamp is recorded to the microsecond, as the
            # adapter-side stamps are (paper_benchmark rounds to 6 places)
            ep = _epoch(v)
            return None if ep is None else round(ep, 6)
        stages = {
            "pinnacle_observed_at": _us(p_obs),
            "ingest_at": _us(probability["received_at"]),
            "probability_qualified_at": _us(cand.get("decided_at")),
            "book_observed_at": _us(book["observed_at"]),
            "decision_start_at": _us(at),
            "basis": {
                "pinnacle_observed_at": "the provider's source stamp",
                "ingest_at": "our receipt of the Pinnacle reading",
                "probability_qualified_at": ("the lane's valuation decision "
                                             "instant (the valuation row's "
                                             "decided_at)"),
                "book_observed_at": "our receipt of the executable book",
                "decision_start_at": "the paper decision instant",
                "clock": ("the decision clock (the pass context's clock; the "
                          "wall clock in production)")}}
        contract = {"us_market_slug": cand.get("us_market_slug"),
                    "fixture": cand.get("fixture"),
                    "condition_id": cand.get("condition_id"),
                    "payout_event": cand.get("payout_event"),
                    "payout_is_complement": cand.get("payout_is_complement"),
                    "sport_family": cand.get("sport_family"),
                    "event_key": (label or {}).get("event_key"),
                    "market": cand.get("market"), "line": cand.get("line"),
                    "scope": cand.get("period")}
        intent = build_decision_intent(
            decision_id=did, strategy=strategy, strategy_version=version,
            opportunity_id=opportunity_key(
                fixture=cand.get("fixture"),
                us_market_slug=cand.get("us_market_slug"),
                holding_side=side, line=cand.get("line"),
                scope=cand.get("period")),
            policy=policy, probability=probability, book=book,
            risk_rails=risk_rails, evidence_refs=refs,
            latency_stages=stages,
            evidence={
                "valuation_id": cand.get("valuation_id"),
                "book_obs_id": None if obs is None else obs["obs_id"],
                "book_observed_at": None if obs is None
                else float(obs["observed_at"]),
                "book_age_at_decision_s": book_age,
                "pinnacle_observed_at": pinnacle.get("observed_at"),
                "pinnacle_received_at": pinnacle.get("received_at"),
                "pinnacle_provider": pinnacle.get("provider"),
                "probability": p,
                "probability_authority": pin.get("probability_authority"),
                "parameters_version": (params or {}).get("version")
                if isinstance(params, dict) else None,
                # R30A review: the build that decided, inside the sha, so a
                # row can be attributed to its decision logic after the fact
                "build": serving_build_identity(),
                # R30C: the expected settlement-exception cost against the
                # completed-game assumption. SHADOW evidence for Eddie /
                # Allie (R30B); it gates nothing and is not one of the
                # parity ledger's evidence ids (`evidence_ids`).
                "settlement_exception_risk": comps.get(
                    "settlement_exception_risk") or unavailable(
                        "COMPONENT_NOT_COMPUTED")},
            opportunity_score=comps["opportunity_score"],
            derek=CC.derek_component(
                verdict=verdict, policy_version=version,
                policy_decision=policy_decision, refusals=refusals,
                economics=e, gross_edge_pp=best_edge, probability=p),
            karen=comps["karen"], allie=allie or None, eddie=eddie or None,
            us_market_slug=cand["us_market_slug"],
            contract=contract,
            holding_side=side, order_intent=cand.get("side"),
            order_type=ent["order_type"], time_in_force=ent["time_in_force"],
            limit_price=sized.get("limit"), wire_price=sized.get("wire"),
            target_qty=sized.get("qty"),
            sizing_basis={
                "rule": "size_within_edge",
                "target_order_usd": ent.get("target_order_usd"),
                "per_order_cap_usd": (cfg.get("risk") or {}).get(
                    "per_order_cap_usd"),
                "budget_usd": sized.get("budget_usd"),
                "levels_used": sized.get("levels_used"),
                "depth_within_limit": sized.get("depth_within_limit"),
                "fee_stop": sized.get("fee_stop"),
                "acquisition_cost_usd": cost},
            created_at=at)
        await record_decision_intent(conn, intent)
        return intent
    except Exception:                                          # noqa: BLE001
        log.warning("canonical intent not built for %s", did, exc_info=True)
        return None


HOOK_NAMES = ("CANONICAL_DECISION", "CANONICAL_ENTRY_ADAPTERS",
              "CANONICAL_MANAGEMENT_RECORD", "CANONICAL_MANAGEMENT_ADAPTERS")
_INSTALL_TASKS: set = set()


def install(get_pool=None, *, process: str = "api") -> None:
    """Install the canonical hooks in this (executing) process
    (execution_intent.start calls it) and record the install durably with
    this process's commit, so the production cutover can prove it."""
    from . import decision_hooks as DH
    DH.CANONICAL_DECISION = canonical_decision
    DH.CANONICAL_ENTRY_ADAPTERS = entry_adapters
    DH.CANONICAL_MANAGEMENT_RECORD = record_management_intent
    DH.CANONICAL_MANAGEMENT_ADAPTERS = management_adapters
    if get_pool is not None:
        try:
            t = asyncio.get_running_loop().create_task(
                _record_install(get_pool, process))
            _INSTALL_TASKS.add(t)
            t.add_done_callback(_INSTALL_TASKS.discard)
        except RuntimeError:
            pass                            # no loop (tests): nothing to record


def installed_hooks() -> list:
    from . import decision_hooks as DH
    return [n for n in HOOK_NAMES if getattr(DH, n, None) is not None]


async def _record_install(get_pool, process: str) -> None:
    import os
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO live_parity_hook_installs (process, commit_sha,
                     hooks) VALUES ($1, $2, $3)""",
                process, os.environ.get("RENDER_GIT_COMMIT"),
                installed_hooks())
    except Exception:                                         # noqa: BLE001
        log.warning("live parity hook install not recorded", exc_info=True)


# ─────────────────────────── the production cutover ────────────────────

CUTOVER_MIGRATIONS = ("225", "226")

# THE DECISION-LOGIC IDENTITY (the pinned decision-path files, the roots
# they are derived from, and their sha256) lives in the PURE module
# decision_logic -- so the profitability validation endpoint, which imports
# no execution module, can check the serving build against the effective
# cutover too. Re-exported here; cutover_checks / readiness_report resolve
# decision_logic_hash through THIS module (tests substitute it here).
from .decision_logic import (  # noqa: E402,F401  (re-exported)
    DECISION_LOGIC_FILES, DECISION_LOGIC_ROOTS, NOT_DECISION_LOGIC,
    decision_logic_hash, decision_logic_imports, serving_build_identity)


def effective_cutover_of(rows: list) -> dict | None:
    """THE EFFECTIVE CUTOVER (pure; the same rule as the view
    live_parity_effective_cutover): rows in recorded order; the latest row
    whose decision_logic_hash differs from its predecessor's (the first row
    differs from nothing)."""
    rs = sorted((dict(r) for r in rows or []),
                key=lambda r: (_epoch(r.get("recorded_at")) or 0.0,
                               r.get("cutover_id") or 0))
    eff, prev = None, object()
    for r in rs:
        if r.get("decision_logic_hash") != prev:
            eff = r
        prev = r.get("decision_logic_hash")
    return eff


async def production_cutover(conn) -> dict | None:
    """THE EFFECTIVE production cutover (the forward window's start), or
    None: the latest release whose decision-logic hash changed
    (live_parity_effective_cutover). `cutover_at` is its recorded_at."""
    try:
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM live_parity_effective_cutover")
    except Exception:                                         # noqa: BLE001
        return None
    return None if r is None else dict(r)


async def latest_release_cutover(conn) -> dict | None:
    """The most recently recorded release (it may not have restarted the
    window), or None."""
    try:
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM live_parity_cutover "
                " ORDER BY recorded_at DESC, cutover_id DESC LIMIT 1")
    except Exception:                                         # noqa: BLE001
        return None
    return None if r is None else dict(r)


async def release_cutover(conn, release_sha: str) -> dict | None:
    """The LATEST recorded deployment of `release_sha`, or None (a sha may
    have several rows: A -> B -> A records A twice)."""
    try:
        async with conn.transaction():
            r = await conn.fetchrow(
                "SELECT * FROM live_parity_cutover WHERE release_sha = $1 "
                " ORDER BY recorded_at DESC, cutover_id DESC LIMIT 1",
                release_sha)
    except Exception:                                         # noqa: BLE001
        return None
    return None if r is None else dict(r)


async def cutover_checks(conn, *, release_sha: str, api_sha: str | None,
                         hooks_here: list, recorded_by: str | None = None,
                         logic: dict | None = None) -> dict:
    """EVERY CUTOVER CONDITION, read from production now (pure reads). Each
    check is {passed, value}; the cutover is recorded only if all pass."""
    checks: dict = {}

    def put(name, passed, value=None):
        checks[name] = {"passed": bool(passed), "value": value}

    full = bool(release_sha) and len(release_sha) == 40 and all(
        c in "0123456789abcdef" for c in release_sha)
    put("RELEASE_SHA_IS_A_FULL_SHA", full, release_sha)
    put("API_RUNS_THE_RELEASE_SHA", api_sha == release_sha, api_sha)
    # R30A: a NAMED HUMAN records a cutover (the table CHECKs the same)
    put("RECORDED_BY_IS_A_NAMED_HUMAN", is_named_human(recorded_by),
        recorded_by)
    lg = logic if logic is not None else decision_logic_hash()
    put("DECISION_LOGIC_HASH_COMPUTED", lg.get("hash") is not None,
        {"hash": lg.get("hash"), "missing": lg.get("missing")})
    wb = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
    wb = json.loads(wb) if isinstance(wb, str) else (wb or {})
    wsha = (wb or {}).get("commit_sha")
    put("WORKERS_RUN_THE_RELEASE_SHA", wsha == release_sha, wsha)
    applied = {str(r["version"])[:3] for r in await conn.fetch(
        "SELECT version FROM schema_migrations")}
    put("MIGRATIONS_225_226_APPLIED",
        all(m in applied for m in CUTOVER_MIGRATIONS),
        sorted(m for m in applied if m >= "225"))
    inst = await conn.fetchrow(
        """SELECT install_id, hooks, installed_at FROM live_parity_hook_installs
            WHERE commit_sha = $1 ORDER BY installed_at DESC LIMIT 1""",
        release_sha)
    put("HOOKS_INSTALLED_ON_THE_RELEASE_SHA",
        inst is not None and set(HOOK_NAMES) <= set(inst["hooks"] or []),
        None if inst is None else {"install_id": inst["install_id"],
                                   "hooks": list(inst["hooks"])})
    put("HOOKS_INSTALLED_IN_THIS_PROCESS",
        set(HOOK_NAMES) <= set(hooks_here), list(hooks_here))
    ctl = await control(conn)
    put("SMALL_LIVE_IS_SHADOW", ctl.get("mode") == MODE_SHADOW
        and SMALL_LIVE_MODE == MODE_SHADOW, ctl.get("mode"))
    put("SMALL_LIVE_NOT_HALTED", not ctl.get("halted"), ctl.get("halted"))
    em = await conn.fetchrow(
        "SELECT enabled, stopped FROM execmirror_control LIMIT 1")
    sent = await conn.fetchval(
        "SELECT count(*) FROM execmirror_orders WHERE venue_order_id IS NOT NULL")
    live_ev = await conn.fetchval("SELECT count(*) FROM small_live_order_events")
    put("NO_CAPITAL_ACTIVATED",
        (em is None or not em["enabled"] or em["stopped"]) and sent == 0
        and live_ev == 0,
        {"execmirror_enabled": None if em is None else em["enabled"],
         "execmirror_stopped": None if em is None else em["stopped"],
         "venue_orders_ever": sent, "small_live_venue_events": live_ev})
    # THE READBACK: every R30 / R30A object present and readable
    objs = ["canonical_decision_intents", "canonical_management_intents",
            "canonical_intent_executions", "live_parity_ledger",
            "small_live_control", "small_live_order_events",
            "live_parity_hook_installs", "agent_work_requests",
            "live_parity_cutover", "live_approvals",
            "live_parity_effective_cutover", "live_approvals_current"]
    present = {o: bool(await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", o)) for o in objs}
    # NO LOGIC DIVERGENCE a human has not cleared. With ONE singleton cutover
    # this read "ever"; with a row per release, "ever" would make every
    # later release unrecordable after the first divergence -- including the
    # release that fixes it. A divergence stays in the ledger forever; what
    # blocks a cutover is one recorded after the last human halt clear (and
    # SMALL_LIVE_NOT_HALTED above refuses while the halt itself stands).
    div = await conn.fetchval(
        "SELECT count(*) FROM live_parity_ledger "
        " WHERE parity_state = 'LOGIC_DIVERGENCE' "
        "   AND ($1::timestamptz IS NULL OR created_at > $1)",
        ctl.get("cleared_at"))
    put("READBACK_OBJECTS_PRESENT", all(present.values()), present)
    put("READBACK_NO_LOGIC_DIVERGENCE", div == 0,
        {"uncleared_divergences": div,
         "since_halt_cleared_at": None if ctl.get("cleared_at") is None
         else ctl["cleared_at"].isoformat()})
    return {"checks": checks,
            "passed": all(c["passed"] for c in checks.values()),
            "install_id": None if inst is None else inst["install_id"],
            "workers_sha": wsha, "logic": lg,
            "migrations": sorted(m for m in applied if m >= "225")}


async def record_cutover(conn, *, release_sha: str, recorded_by: str,
                         api_sha: str | None = None,
                         hooks_here: list | None = None) -> dict:
    """RECORD THIS DEPLOYMENT'S PRODUCTION CUTOVER (one append-only row per
    deployment of a release), only if every condition holds now. Called
    INSIDE THE SERVING PROCESS (POST /api/admin/live-parity/cutover): the
    API's commit and the installed hooks are this process's own, and the
    decision-logic hash is computed from the files this process runs.

    ALREADY RECORDED means: the LATEST recorded cutover is this release (a
    repeated call for the same deployment is returned unchanged; the table's
    trigger refuses a consecutive duplicate as well). R30A review: this used
    to return `already` for ANY earlier row of the sha, so a rollback /
    redeploy of an earlier release (A -> B -> A) could not be recorded and
    the forward window kept counting A's logic as B's evidence. Now the
    third deployment appends its own row, and the effective cutover moves to
    it when its decision_logic_hash differs from B's. Returns {recorded,
    cutover, effective, restarts_forward_window, checks}."""
    latest = await latest_release_cutover(conn)
    if latest is not None and latest.get("release_sha") == release_sha:
        return {"recorded": False, "already": True, "cutover": latest,
                "already_rule": "THIS_RELEASE_IS_THE_LATEST_RECORDED_CUTOVER",
                "effective": await production_cutover(conn)}
    api = api_sha if api_sha is not None else os.environ.get("RENDER_GIT_COMMIT")
    hooks = installed_hooks() if hooks_here is None else hooks_here
    got = await cutover_checks(conn, release_sha=release_sha, api_sha=api,
                               hooks_here=hooks, recorded_by=recorded_by)
    if not got["passed"]:
        return {"recorded": False, "refused": [
            k for k, c in got["checks"].items() if not c["passed"]],
            "checks": got["checks"]}
    before = await production_cutover(conn)
    lg = got["logic"]
    try:
        async with conn.transaction():
            cid = await conn.fetchval(
                """INSERT INTO live_parity_cutover (release_sha, api_sha,
                     workers_sha, migrations, decision_logic_hash,
                     decision_logic_files, hook_install_id, small_live_mode,
                     small_live_halted, capital_activated, evidence,
                     recorded_by)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, 'SHADOW', false,
                           false, $8::jsonb, $9)
                   RETURNING cutover_id""",
                release_sha, api, got["workers_sha"], got["migrations"],
                lg["hash"], _j(lg["files"]), got["install_id"],
                _j(got["checks"]), recorded_by)
    except Exception as exc:                                  # noqa: BLE001
        if "LIVE_PARITY_CUTOVER_ALREADY_LATEST" not in str(exc):
            raise
        # a concurrent recorder appended this deployment first
        return {"recorded": False, "already": True,
                "cutover": await latest_release_cutover(conn),
                "already_rule": "THIS_RELEASE_IS_THE_LATEST_RECORDED_CUTOVER",
                "effective": await production_cutover(conn)}
    row = await conn.fetchrow(
        "SELECT * FROM live_parity_cutover WHERE cutover_id = $1", cid)
    row = None if row is None else dict(row)
    eff = await production_cutover(conn)
    return {"recorded": True, "cutover": row, "effective": eff,
            "restarts_forward_window": (
                eff is not None and row is not None
                and eff.get("cutover_id") == row.get("cutover_id")),
            "previous_effective": before, "checks": got["checks"]}


# ─────────────────────────── convergence: ONE origin of live exposure ──
#
# R30A / audit P0 #2. EVERY PLACE A NEW VENUE ORDER CAN BE CREATED, and what
# stands in front of it. R30A review: the first version of this list named
# three "generations" (execmirror, the ACTUAL sibling, the funded stack) and
# called that the whole surface; the repository's own scanner
# (submission_surface.mutation_callers) also finds live_executor's copy,
# manual-desk and GTC lanes, the mirror lane, the underdog sleeve and
# calibration reaching pmus.submit_fok with sell=False, and a CLOB post_order
# path -- none behind a canonical intent. The census is now by VENUE
# PRIMITIVE (the only code that creates an order), so a new lane is covered
# the moment it calls one:
#
#   pmus.submit_fok           polymarket-us, every caller (copy / manual /
#                             GTC / mirror / underdog / calibration / funded):
#                             a BUY raises execution_gate.Denied(canonical_
#                             origination_required) inside the adapter unless
#                             it carries the canonical LiveAuthorization
#                             (pmus.require_canonical_origination). Sells
#                             (exits, reductions, protection) pass.
#   live_executor._submit_fok polymarket-CLOB (a second venue): the same
#                             boundary before its client is built.
#   execmirror.Venue.place    the mirror account: LegacyOriginationRetired
#                             without the authorization; a planned copy is
#                             claimed, refused before the client, recorded
#                             REJECTED, never retried. cancel / close pass.
#   kalshi_venue.KalshiClient.submit
#                             UNREACHABLE: no module outside the Kalshi set
#                             imports kalshi_venue and nothing calls submit
#                             (tests/test_kalshi_isolation.py); its own gates
#                             (credential, env switch, durable control, fresh
#                             reconciliation) stand regardless.
#
# Upstream of the primitives, the two lanes that would carry a canonical
# intent ask for it explicitly, before they write anything:
#
#   execution_intent          the ACTUAL sibling: ActualLane._run calls
#                             authorize_live_exposure BEFORE its claim (the
#                             intent must exist, verify, be the one named,
#                             match all ten order fields, be unexpired, pass
#                             the LIVE policy and the live gates, and carry
#                             the canonical authorization).
#   the funded stack          bettor_funded_execution: every acquisition
#                             passes authorize_live_exposure on its full
#                             order (canonical_order_of) before anything is
#                             written or sent, and hands the token on to
#                             pmus.submit_fok; exits are untouched.
#
# In THIS release SMALL_LIVE_MODE is SHADOW (and migration 225 CHECKs it),
# so issue_live_authorization never issues and every primitive refuses every
# BUY before the venue. Turning LIVE on is a new release, a new migration
# and the owner's explicit approval -- and even then the only origin is the
# canonical intent. No third path is built: the canonical SMALL LIVE adapter
# remains the one designated originator. tests/test_live_parity_convergence.
# py walks the AST for every order-creating call and pins this census.

R_NO_CANONICAL = "NO_CANONICAL_DECISION_INTENT"
R_CANONICAL_SHA = "CANONICAL_INTENT_SHA_DOES_NOT_VERIFY"
R_CANONICAL_NOT_NAMED = "EXECUTION_DOES_NOT_NAME_THIS_CANONICAL_INTENT"
R_CANONICAL_MISMATCH = "ORDER_DIFFERS_FROM_ITS_CANONICAL_INTENT"
R_NO_LIVE_AUTHORIZATION = "CANONICAL_LIVE_AUTHORIZATION_ABSENT_SMALL_LIVE_IS_SHADOW"


#: the authorization object itself lives in the PURE module live_authorization
#: (the venue adapters, reachable from the workers, import only that)
from .live_authorization import LiveAuthorization  # noqa: E402,F401


def issue_live_authorization(intent: dict, *, governance: dict,
                             now: float) -> LiveAuthorization | None:
    """THE CANONICAL SMALL LIVE ADAPTER'S AUTHORIZATION (pure). SHADOW:
    never issued. Outside SHADOW (a future, owner-approved release) only for
    a governance-admissible intent."""
    if SMALL_LIVE_MODE == MODE_SHADOW:
        return None
    if not (governance or {}).get("admissible"):
        return None
    return LiveAuthorization(intent_id=intent["intent_id"],
                             content_sha=intent["content_sha"],
                             issued_at=float(now))


def canonical_live_authorized(token) -> bool:
    """True only for an authorization the canonical SMALL LIVE adapter
    issued in LIVE mode. Always False in this (SHADOW) release."""
    from . import live_authorization as LA
    return LA.authorized(token, mode=SMALL_LIVE_MODE)


#: the order fields a live order must share with its canonical intent
#: (order field -> intent field)
ORIGINATION_MATCH = {"us_market_slug": "us_market_slug",
                     "order_intent": "order_intent",
                     "holding_side": "holding_side", "strategy": "strategy",
                     "strategy_version": "strategy_version",
                     "time_in_force": "time_in_force",
                     "order_type": "order_type",
                     "wire_price": "wire_price", "limit_price": "limit_price",
                     "target_qty": "target_qty"}


#: VENUE-FORM fields a live order may present instead (the funded stack
#: speaks the venue's vocabulary, not the intent's): order field -> how the
#: canonical intent determines it. R30A review: the funded boundary bound
#: only the slug and the order intent, so a funded BUY of any quantity,
#: price or strategy on a canonical intent's market passed the match.
VENUE_ORIGINATION_MATCH = {
    "venue_time_in_force": "execmirror.TIF[intent.time_in_force]",
    "post_only": "intent.order_type == 'RESTING' (execmirror.venue_params)",
    "live_qty": ("the intent's target_qty at the canonical capital scale "
                 "(execmirror.scale_qty, the SMALL LIVE adapter's own rule)")}


def origination_mismatches(intent: dict, order: dict, *,
                           scale=None) -> list:
    """The order fields (of those `order` names) that differ from the
    canonical intent (pure). Numbers compare as decimals. Venue-form fields
    (VENUE_ORIGINATION_MATCH) compare against what the intent determines;
    `live_qty` needs the canonical capital `scale` (unknown -> mismatch)."""
    out = []
    for of, inf in ORIGINATION_MATCH.items():
        if of not in order:
            continue
        a, b = order.get(of), intent.get(inf)
        if of in ("wire_price", "limit_price", "target_qty"):
            da, db = _dec(a), _dec(b)
            same = (da is None and db is None) or (
                da is not None and db is not None and da == db)
        else:
            same = (None if a is None else str(a)) == (
                None if b is None else str(b))
        if not same:
            out.append({"field": of, "order": None if a is None else str(a),
                        "intent": None if b is None else str(b)})
    for of in VENUE_ORIGINATION_MATCH:
        if of not in order:
            continue
        a = order.get(of)
        if of == "venue_time_in_force":
            want = M.TIF.get(str(intent.get("time_in_force")))
            same = want is not None and str(a) == str(want)
        elif of == "post_only":
            want = str(intent.get("order_type")) == "RESTING"
            same = a is want
        else:
            if scale is None or intent.get("target_qty") is None:
                want, same = "SCALE_UNREADABLE", False
            else:
                want = int(M.scale_qty(intent["target_qty"], scale)[1])
                same = _dec(a) is not None and _dec(a) == Decimal(want)
        if not same:
            out.append({"field": of, "order": None if a is None else str(a),
                        "intent": None if want is None else str(want)})
    return out


async def _canonical_scale(conn):
    """The canonical SMALL LIVE adapter's capital scale (execmirror_control,
    read-only), or None when unreadable."""
    try:
        async with conn.transaction():                        # a savepoint
            v = await conn.fetchval(
                "SELECT scale FROM execmirror_control LIMIT 1")
    except Exception:                                         # noqa: BLE001
        return None
    return None if v is None else Decimal(str(v))


async def authorize_live_exposure(conn, *, decision_id: str | None = None,
                                  canonical_intent_id: str | None = None,
                                  named: dict | None = None,
                                  order: dict | None = None,
                                  now: float | None = None) -> dict:
    """MAY THIS NEW LIVE EXPOSURE BE ORIGINATED? Read-only; never raises.

    {ok, refusal, detail, token}. In order: the canonical intent (by
    decision or id) exists; its sha verifies; the caller names THIS intent
    (`named`: {intent_id, content_sha}); the order matches it
    (origination_mismatches); it is unexpired at `now`; the LIVE policy and
    the live gates admit it (governance_verdict over the approvals in force);
    and the canonical SMALL LIVE adapter issues its authorization -- which it
    never does in SHADOW."""
    t = time.time() if now is None else float(now)

    def no(code, **detail):
        return {"ok": False, "refusal": code, "detail": detail, "token": None}
    try:
        async with conn.transaction():                        # a savepoint
            if canonical_intent_id is not None:
                row = await conn.fetchrow(
                    "SELECT * FROM canonical_decision_intents "
                    " WHERE intent_id = $1", canonical_intent_id)
            elif decision_id is not None:
                row = await conn.fetchrow(
                    "SELECT * FROM canonical_decision_intents "
                    " WHERE decision_id = $1", decision_id)
            else:
                row = None
    except Exception as exc:                                  # noqa: BLE001
        return no(R_NO_CANONICAL, error=type(exc).__name__)
    if row is None:
        return no(R_NO_CANONICAL, decision_id=decision_id,
                  canonical_intent_id=canonical_intent_id)
    ci = dict(row)
    if not verify_intent(ci):
        return no(R_CANONICAL_SHA, intent_id=ci["intent_id"])
    nm = named or {}
    if nm.get("intent_id") != ci["intent_id"] or \
            nm.get("content_sha") != ci["content_sha"]:
        return no(R_CANONICAL_NOT_NAMED, named=nm,
                  canonical={"intent_id": ci["intent_id"],
                             "content_sha": ci["content_sha"]})
    scale = (await _canonical_scale(conn)
             if "live_qty" in (order or {}) else None)
    mism = origination_mismatches(ci, order or {}, scale=scale)
    if mism:
        return no(R_CANONICAL_MISMATCH, mismatches=mism,
                  intent_id=ci["intent_id"])
    exp = intent_expiry_refusal(ci, now=t)
    if exp is not None:
        return no(exp, expires_at=_epoch(ci.get("expires_at")), now=t)
    try:
        gin = await governance_in_force(conn)
    except Exception as exc:                                  # noqa: BLE001
        gin = None
        log.debug("approvals unreadable: %s", type(exc).__name__)
    gov = governance_verdict(ci, gin)
    if not gov["admissible"]:
        return no(gov["refusals"][0], governance=gov,
                  intent_id=ci["intent_id"])
    token = issue_live_authorization(ci, governance=gov, now=t)
    if not canonical_live_authorized(token):
        return no(R_NO_LIVE_AUTHORIZATION, mode=SMALL_LIVE_MODE,
                  intent_id=ci["intent_id"])
    return {"ok": True, "refusal": None, "token": token,
            "detail": {"intent_id": ci["intent_id"],
                       "content_sha": ci["content_sha"]}}


# ─────────────────────────── the latency chain (R30A section 6) ────────

#: the stages, in order, and the consecutive / end-to-end spans reported
LATENCY_STAGES = ("pinnacle_observed_at", "ingest_at",
                  "probability_qualified_at", "book_observed_at",
                  "decision_start_at", "intent_recorded_at",
                  "paper_submit_at", "paper_fill_at")
#: span kinds. ELAPSED: the later stage always follows the earlier one; a
#: negative value is excluded and counted by cause -- CLOCK_DISAGREEMENT only
#: when the two stamps come from DIFFERENT clocks (the provider's source
#: stamp against ours), STAGE_ORDER_INVERTED when both are our own clock.
#: SIGNED: both stamps are ours and either order is legitimate, so the
#: signed value is the measurement and negatives stay in the distribution.
ELAPSED, SIGNED = "ELAPSED", "SIGNED"
PROVIDER_CLOCK, OUR_CLOCK = "PROVIDER_CLOCK", "OUR_CLOCK"
#: which clock stamped each stage
STAGE_CLOCK = {"pinnacle_observed_at": PROVIDER_CLOCK}
#: (name, from, to, kind). R30A review: the book stage was reported as
#: book_observed -> decision_start, an ELAPSED span; but decide_one stamps
#: decision_start and THEN reads the book, so a normal fresh read gives a
#: negative span, which was discarded as CLOCK_DISAGREEMENT although both
#: stamps are our clock (16 of 54 on r30a_intent) -- biasing the
#: distribution toward cached / stream books. Now the book is reported as
#: its SIGNED age at decision start (negative = read after the decision
#: started) and as the ELAPSED book_observed -> intent_recorded span.
LATENCY_SPANS = (
    ("pinnacle_to_ingest", "pinnacle_observed_at", "ingest_at", ELAPSED),
    ("ingest_to_probability_qualified", "ingest_at",
     "probability_qualified_at", ELAPSED),
    ("probability_qualified_to_decision_start", "probability_qualified_at",
     "decision_start_at", ELAPSED),
    ("book_age_at_decision_start", "book_observed_at", "decision_start_at",
     SIGNED),
    ("book_observed_to_intent_recorded", "book_observed_at",
     "intent_recorded_at", ELAPSED),
    ("decision_start_to_intent_recorded", "decision_start_at",
     "intent_recorded_at", ELAPSED),
    ("intent_recorded_to_paper_submit", "intent_recorded_at",
     "paper_submit_at", ELAPSED),
    ("paper_submit_to_paper_fill", "paper_submit_at", "paper_fill_at",
     ELAPSED),
    ("pinnacle_to_paper_submit", "pinnacle_observed_at", "paper_submit_at",
     ELAPSED),
    ("decision_start_to_paper_submit", "decision_start_at",
     "paper_submit_at", ELAPSED))


def _pct(xs: list, q: float):
    if not xs:
        return None
    s = sorted(xs)
    i = (len(s) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 6)


#: stage stamps are recorded to the microsecond; a "negative" span smaller
#: than that is the rounding of two stamps of one instant, not two clocks
LATENCY_RESOLUTION_S = 1e-6


def latency_report(rows: list) -> dict:
    """PER-STAGE LATENCY DISTRIBUTIONS (pure). `rows` are one dict per
    decision with the stage stamps (epoch seconds, or None with a reason in
    `<stage>_why`). For each span: n, p50, p90, max (seconds); a span whose
    stamps are missing is UNAVAILABLE for that decision and counted by
    reason. A NEGATIVE value of an ELAPSED span is never folded into the
    distribution: it is counted as CLOCK_DISAGREEMENT when the two stamps
    come from different clocks, STAGE_ORDER_INVERTED when both are ours. A
    SIGNED span keeps its sign (min reported too)."""
    spans = {}
    for name, a, b, kind in LATENCY_SPANS:
        vals, why = [], {}
        cross = STAGE_CLOCK.get(a, OUR_CLOCK) != STAGE_CLOCK.get(b, OUR_CLOCK)
        for r in rows or []:
            ta, tb = _epoch(r.get(a)), _epoch(r.get(b))
            if ta is None or tb is None:
                miss = a if ta is None else b
                reason = r.get(miss + "_why") or "%s_NOT_RECORDED" % miss.upper()
                why[reason] = why.get(reason, 0) + 1
                continue
            d = tb - ta
            if d < 0 and kind == ELAPSED:
                if d > -LATENCY_RESOLUTION_S:
                    d = 0.0
                else:
                    cause = ("CLOCK_DISAGREEMENT" if cross
                             else "STAGE_ORDER_INVERTED")
                    why[cause] = why.get(cause, 0) + 1
                    continue
            vals.append(d)
        spans[name] = {
            "from": a, "to": b, "kind": kind,
            "clocks": ("%s->%s" % (STAGE_CLOCK.get(a, OUR_CLOCK),
                                   STAGE_CLOCK.get(b, OUR_CLOCK))),
            "n": len(vals),
            "p50_s": _pct(vals, 0.5), "p90_s": _pct(vals, 0.9),
            "max_s": None if not vals else round(max(vals), 6),
            "min_s": (None if not vals or kind != SIGNED
                      else round(min(vals), 6)),
            "status": "MEASURED" if vals else "UNAVAILABLE",
            "unavailable": why}
    return {"version": "CANONICAL_LATENCY_CHAIN_V2",
            "decisions": len(rows or []),
            "stages": list(LATENCY_STAGES), "spans": spans,
            "units": "seconds",
            "rule": ("stages come from the canonical intent (decision side), "
                     "the PAPER adapter record (intent recorded, paper "
                     "submit) and the paper fill ledger (first fill of the "
                     "entry order); a missing stage is UNAVAILABLE with its "
                     "reason, never a zero; CLOCK_DISAGREEMENT is reserved "
                     "for stamps from different clocks; the book stage is "
                     "its SIGNED age at decision start (negative: the book "
                     "was read after the decision started)")}


async def latency_rows(conn, *, since: float | None = None,
                       limit: int = 500) -> list:
    """One row of stage stamps per canonical decision intent (read-only).

    R30A review: this ran two correlated subqueries per intent, each a
    sequential scan of paper_orders (and paper_fills), under a 6 s statement
    timeout with up to 5,000 intents -- the read-only endpoint would time out
    at production size. Now the window's intents are selected ONCE, and
    each joins its PAPER adapter record (unique index) and ONE lateral
    aggregate of its entry orders and their first fill, on
    paper_orders (decision_id, role) and paper_fills (order_id) -- both
    indexed by migration 225."""
    rows = await conn.fetch(
        """WITH sel AS (
               SELECT i.intent_id, i.decision_id, i.latency_stages,
                      i.created_at
                 FROM canonical_decision_intents i
                WHERE ($1::double precision IS NULL
                       OR i.created_at >= to_timestamp($1))
                ORDER BY i.created_at DESC LIMIT $2)
           SELECT sel.intent_id, sel.decision_id, sel.latency_stages,
                  e.refs AS paper_refs, ent.first_fill_at, ent.entry_orders
             FROM sel
             LEFT JOIN canonical_intent_executions e
               ON e.intent_id = sel.intent_id AND e.adapter = 'PAPER'
             LEFT JOIN LATERAL (
                  SELECT count(DISTINCT o.order_id) AS entry_orders,
                         min(f.filled_at) AS first_fill_at
                    FROM paper_orders o
                    LEFT JOIN paper_fills f ON f.order_id = o.order_id
                   WHERE o.decision_id = sel.decision_id
                     AND o.role = 'ENTRY') ent ON true
            ORDER BY sel.created_at DESC""", since, int(limit))
    out = []
    for r in rows:
        st = _obj(r["latency_stages"])
        refs = _obj(r["paper_refs"])
        ps = refs.get("stages") or {}
        d = {k: st.get(k) for k in LATENCY_STAGES[:5]}
        d["intent_id"] = r["intent_id"]
        d["intent_recorded_at"] = ps.get("intent_recorded_at")
        d["paper_submit_at"] = ps.get("paper_submit_at")
        if r["paper_refs"] is None:
            d["intent_recorded_at_why"] = "NO_PAPER_ADAPTER_RECORD"
            d["paper_submit_at_why"] = "NO_PAPER_ADAPTER_RECORD"
        elif ps.get("paper_submit_at") is None and ps.get("paper_submit_why"):
            d["paper_submit_at_why"] = ps["paper_submit_why"]
        d["paper_fill_at"] = _epoch(r["first_fill_at"])
        if d["paper_fill_at"] is None:
            d["paper_fill_at_why"] = ("NO_ENTRY_ORDER" if not r["entry_orders"]
                                      else "NOT_FILLED_YET_OR_UNFILLED")
        out.append(d)
    return out


def uninstall() -> None:
    from . import decision_hooks as DH
    DH.CANONICAL_DECISION = None
    DH.CANONICAL_ENTRY_ADAPTERS = None
    DH.CANONICAL_MANAGEMENT_RECORD = None
    DH.CANONICAL_MANAGEMENT_ADAPTERS = None
