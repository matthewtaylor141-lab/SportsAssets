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
  LOGIC_DIVERGENCE             anything else: a different intent sha, action,
                               side, limit, order form, strategy, evidence, or
                               a quantity that is NOT the scaled quantity.
                               It HALTS SMALL LIVE immediately (same
                               transaction); a named human clears it.

A PAPER fill is never a SMALL LIVE fill: the SMALL LIVE lifecycle
(REJECTED/RESTING/PARTIAL/FILLED/CANCEL_PENDING/CANCELLED) is read only from
the venue's own order record (`live_state_of`, small_live_order_events).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from . import execmirror as M
from . import canonical_intent as SLV  # the sleeve constants
from .canonical_intent import (  # noqa: F401  (re-exported)
    ACT_CANCEL_FIRST, ACT_EXIT, ACT_NONE, ACT_PROTECT, ACT_REDUCE,
    INTENT_VERSION, MGMT_INTENT_VERSION, VENUE, _INTENT_FIELDS, _MGMT_FIELDS,
    _dec, _norm, build_decision_intent, build_management_intent,
    canonical_json, content_sha, decision_intent_id, management_action,
    management_intent_id, sleeve_of, unavailable,
    verify_intent)

#: what the PAPER adapter reads from the intent (canonical_intent)
paper_entry_fields = SLV.paper_entry_fields

log = logging.getLogger(__name__)

PAPER_ADAPTER_VERSION = "PAPER_ADAPTER_V1"
LIVE_ADAPTER_VERSION = "SMALL_LIVE_ADAPTER_V1"
PARITY_VERSION = "LIVE_PARITY_V1"
READINESS_VERSION = "LIVE_READINESS_GATE_V1"

#: THE ONLY SMALL LIVE MODE THIS CODE KNOWS. The database CHECKs the same.
MODE_SHADOW = "SHADOW"
SMALL_LIVE_MODE = MODE_SHADOW

# parity states (migration 225)
MATCHED = "MATCHED"
SCALE = "EXPECTED_SCALE_DIFFERENCE"
VENUE_DIFF = "VENUE_EXECUTION_DIFFERENCE"
DIVERGENCE = "LOGIC_DIVERGENCE"
PARITY_STATES = (MATCHED, SCALE, VENUE_DIFF, DIVERGENCE)


# adapter states (migration 225)
P_SUBMITTED, P_REFUSED, P_NO_ORDER = ("PAPER_SUBMITTED", "PAPER_REFUSED",
                                      "PAPER_NO_ORDER")
S_PROPOSED, S_EXCLUDED, S_NO_ORDER, S_HALTED = (
    "SHADOW_PROPOSED", "SHADOW_EXCLUDED", "SHADOW_NO_ORDER", "SHADOW_HALTED")

#: live exclusions that are capital/scale bounds, not logic
CAPITAL_EXCLUSIONS = {M.BELOW_VENUE_MINIMUM, M.ABOVE_ORDER_CAP,
                      M.INSUFFICIENT_CASH, M.NO_LIVE_INVENTORY}
#: live exclusions that are venue facts, not logic
VENUE_EXCLUSIONS = {M.UNSUPPORTED_ORDER, M.INVENTORY_COMMITTED,
                    "ACCOUNT_STATE_NOT_CURRENT"}
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


def paper_entry_request(intent: dict, order: dict) -> dict:
    """The PAPER adapter's requested entry order, normalized."""
    return _req(action="BUY_ENTRY", intent=order.get("intent"),
                side=order.get("holding_side"),
                slug=order.get("us_market_slug"), qty=order.get("qty"),
                limit=order.get("limit_price"), wire=order.get("wire_price"),
                tif=str(order.get("time_in_force")),
                order_type=str(order.get("order_type")),
                strategy=order.get("strategy"),
                strategy_version=intent["strategy_version"],
                evidence_ids=evidence_ids(intent), sha=intent["content_sha"])


def live_entry_proposal(intent: dict, *, scale, buying_power,
                        max_order_usd, halted: bool = False) -> dict:
    """THE SMALL LIVE ADAPTER, ENTRY (pure, SHADOW): the real venue order the
    SAME intent produces at capital scale. Never sent."""
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
    return dict(base, state=S_PROPOSED if plan.state == "PLANNED"
                else S_EXCLUDED, exclusion=plan.exclusion, requested=req,
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
                paper_taken=(taken or {}).get("taken"))


def hypothetical_live_inventory(open_qty, scale) -> int:
    """SHADOW ONLY: the live position the scaled entry would hold. In LIVE
    mode the adapter must read the venue's inventory instead; this function
    refuses to run outside SHADOW."""
    assert SMALL_LIVE_MODE == MODE_SHADOW, "hypothetical inventory is SHADOW-only"
    return int(M.scale_qty(open_qty, scale)[1])


def live_management_proposal(mi: dict, *, scale, open_qty,
                             halted: bool = False) -> dict:
    """THE SMALL LIVE ADAPTER, MANAGEMENT (pure, SHADOW): the venue order the
    SAME management intent produces on the scaled position. Never sent."""
    assert SMALL_LIVE_MODE == MODE_SHADOW
    base = {"scale": str(_dec(scale)),
            "inventory_basis": "HYPOTHETICAL_SCALED_PAPER_POSITION_SHADOW_ONLY"}
    if halted:
        return dict(base, state=S_HALTED, exclusion="SMALL_LIVE_HALTED",
                    requested=None, params=None, live_qty=0)
    tl = mi.get("target_limit") or {}
    held = hypothetical_live_inventory(open_qty, scale)
    act = mi["action"]

    def req(qty):
        return _req(action=act, intent=mi.get("order_intent"),
                    side=mi["holding_side"], slug=mi["us_market_slug"],
                    qty=qty, limit=tl.get("limit_price"),
                    wire=tl.get("wire_price"), tif=tl.get("time_in_force"),
                    order_type=tl.get("order_type"), strategy=mi.get("strategy"),
                    strategy_version=None,
                    evidence_ids={"valuation_id": mi.get("valuation_id"),
                                  "evidence_version": mi.get("evidence_version")},
                    sha=mi["content_sha"])

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


def compare(*, kind: str, intent: dict, paper: dict, live: dict, scale,
            open_qty=None) -> dict:
    """CLASSIFY ONE PAPER / SMALL LIVE PAIR (pure). `paper` and `live` are the
    adapter records: {state, exclusion|refusal, requested}. Returns
    {parity_state, divergence_fields, comparison}."""
    pr, lr = paper.get("requested") or {}, live.get("requested") or {}
    fields: dict[str, Any] = {}
    div: list[str] = []
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
    excl = live.get("exclusion")
    pref = paper.get("refusal")
    cls = None
    if div:
        cls = DIVERGENCE
    elif live.get("state") == S_PROPOSED and exp is not None and lq != exp:
        div.append("qty")
        cls = DIVERGENCE
    elif live.get("state") == S_EXCLUDED:
        if excl in CAPITAL_EXCLUSIONS:
            cls = SCALE
        elif excl in VENUE_EXCLUSIONS:
            cls = VENUE_DIFF
        else:
            div.append("live_exclusion:%s" % excl)
            cls = DIVERGENCE
    elif paper.get("state") == P_REFUSED and live.get("state") == S_PROPOSED:
        if pref in PAPER_CAPITAL_REFUSALS:
            cls = SCALE
        else:
            div.append("paper_refusal:%s" % pref)
            cls = DIVERGENCE
    elif kind == "DECISION" and not paper_order and \
            live.get("state") == S_PROPOSED and paper.get("state") != P_REFUSED:
        div.append("paper_order_absent")
        cls = DIVERGENCE
    if cls is None:
        same_qty = (pr.get("qty") is None and lq in (0, None)) or (
            pr.get("qty") is not None and lq is not None
            and Decimal(str(pr["qty"])) == Decimal(int(lq)))
        cls = MATCHED if same_qty else SCALE
    return {"parity_state": cls, "divergence_fields": div,
            "comparison": {"fields": fields, "paper_state": paper.get("state"),
                           "live_state": live.get("state"),
                           "live_exclusion": excl, "paper_refusal": pref,
                           "version": PARITY_VERSION}}


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

def readiness(rows: list, *, halted: bool, profitability: dict | None = None,
              min_decisions: int = MIN_DECISION_SAMPLE,
              min_management: int = MIN_MANAGEMENT_SAMPLE) -> dict:
    """THE LIVE READINESS GATE over the forward parity ledger (rows oldest
    first; the caller passes only rows since the cutover / last cleared halt).
    The sample is CONSECUTIVE: a LOGIC_DIVERGENCE anywhere in it fails the
    gate. Activation evidence counts the INVESTMENT sleeve only."""
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
    dec_match = sum(1 for r in dec if r.get("parity_state") != DIVERGENCE)
    mgt_match = sum(1 for r in mgt if r.get("parity_state") != DIVERGENCE)
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
        "xavier_management_match": {"matched": mgt_match, "of": len(mgt),
                                    "rate": rate(mgt_match, len(mgt))},
        "matched": count(inv, MATCHED),
        "expected_scale_differences": count(inv, SCALE),
        "venue_only_differences": count(inv, VENUE_DIFF),
        "logic_divergences": div,
        "excluded_from_activation_evidence": {
            "non_investment_rows": len(rows) - len(inv)},
        "rule": ("READY_FOR_TINY_PILOT only when: a production cutover is "
                 "recorded; SMALL LIVE is not halted; >= %d consecutive "
                 "INVESTMENT decision intents and >= %d INVESTMENT "
                 "management intents compared since it (a MINIMUM PARITY "
                 "SAMPLE ONLY, not profit evidence); zero LOGIC_DIVERGENCE; "
                 "and, separately, the INVESTMENT sleeve's forward "
                 "profitability verdict is SUPPORTED_BY_FORWARD_EVIDENCE "
                 "(positive forward net, positive t and bootstrap lower "
                 "bounds on per-event net, bounded drawdown, >= 30 "
                 "independent resolved events)"
                 % (min_decisions, min_management)),
    }
    blockers = []
    if halted:
        blockers.append("SMALL_LIVE_HALTED_BY_LOGIC_DIVERGENCE")
    if div:
        blockers.append("LOGIC_DIVERGENCES_IN_SAMPLE:%d" % div)
    if len(dec) < min_decisions:
        blockers.append("DECISION_SAMPLE:%d_OF_%d" % (len(dec), min_decisions))
    if len(mgt) < min_management:
        blockers.append("MANAGEMENT_SAMPLE:%d_OF_%d" % (len(mgt),
                                                        min_management))
    pv = (profitability or {}).get("profitability_verdict")
    if pv != "SUPPORTED_BY_FORWARD_EVIDENCE":
        blockers.append("PROFITABILITY:%s" % (pv or "NOT_EVALUATED"))
    report["parity_gate"] = "PASS" if not [b for b in blockers
                                           if not b.startswith("PROFIT")] \
        else "FAIL"
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
                 decision_id, strategy, strategy_version, sleeve, evidence,
                 opportunity_score, derek, karen, allie, eddie, venue,
                 us_market_slug, contract, holding_side, order_intent,
                 order_type, time_in_force, limit_price, wire_price, target_qty,
                 sizing_basis, created_at, content_sha)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,
                 $10::jsonb,$11::jsonb,$12::jsonb,$13,$14,$15::jsonb,$16,$17,
                 $18,$19,$20,$21,$22,$23::jsonb,to_timestamp($24),$25)
               ON CONFLICT (decision_id) DO NOTHING RETURNING intent_id""",
            intent["intent_id"], intent["intent_version"], intent["decision_id"],
            intent["strategy"], intent["strategy_version"], intent["sleeve"],
            _j(intent["evidence"]), _j(intent["opportunity_score"]),
            _j(intent["derek"]), _j(intent["karen"]), _j(intent["allie"]),
            _j(intent["eddie"]), intent["venue"], intent["us_market_slug"],
            _j(intent["contract"]), intent["holding_side"],
            intent["order_intent"], intent["order_type"],
            intent["time_in_force"], intent["limit_price"],
            intent["wire_price"], intent["target_qty"],
            _j(intent["sizing_basis"]), intent["created_at"],
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
                 alternatives, freshness, reason, created_at, content_sha)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                 $17,$18::jsonb,$19::jsonb,$20::jsonb,$21::jsonb,
                 to_timestamp($22),$23)
               ON CONFLICT (review_id) DO NOTHING RETURNING intent_id""",
            mi["intent_id"], mi["intent_version"], mi["review_id"],
            mi["group_id"], mi["position_key"], mi["strategy"], mi["sleeve"],
            mi["valuation_id"], mi["evidence_version"], mi["evidence_state"],
            mi["recommendation"], mi["mechanical_selection"], mi["action"],
            mi["us_market_slug"], mi["holding_side"], mi["order_intent"],
            mi["target_qty"], _j(mi["target_limit"]), _j(mi["alternatives"]),
            _j(mi["freshness"]), _j(mi["reason"]), mi["created_at"],
            mi["content_sha"])
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


async def entry_adapters(conn, intent: dict, *, paper_order: dict,
                         paper_result: dict) -> dict:
    """Record what BOTH adapters did with one canonical decision intent and
    the parity between them. The PAPER adapter already ran (the caller
    submitted `paper_order`, built from `paper_entry_fields(intent)`); the
    SMALL LIVE adapter constructs its SHADOW proposal here. Never raises into
    the caller."""
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
                  "decision_id": intent["decision_id"]})
        live = live_entry_proposal(intent, scale=cap["scale"],
                                   buying_power=cap["buying_power"],
                                   max_order_usd=cap["max_order_usd"],
                                   halted=bool(ctl.get("halted")))
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
                  "buying_power_current": cap["buying_power"] is not None})
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
                  "detail": live.get("detail")})
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


async def readiness_report(conn, *, since: float | None = None,
                           profitability: dict | None = None) -> dict:
    """The gate over the ledger since `since` (default: the last cleared halt,
    else everything)."""
    ctl = await control(conn)
    cut = await production_cutover(conn)
    cut_at = None if cut is None else cut["cutover_at"].timestamp()
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
                        profitability=profitability)
        out["blockers"].insert(0, "NO_PRODUCTION_CUTOVER_RECORDED")
        out["recommendation"] = NOT_READY
        out["since"] = None
        out["cutover"] = None
        out["control"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                          for k, v in ctl.items()}
        return out
    rows = await conn.fetch(
        """SELECT intent_kind, sleeve, parity_state, divergence_fields,
                  comparison, created_at FROM live_parity_ledger
            WHERE ($1::double precision IS NULL OR created_at >= to_timestamp($1))
            ORDER BY created_at, parity_id""", since)
    recs = []
    for r in rows:
        d = dict(r)
        if isinstance(d["comparison"], str):
            d["comparison"] = json.loads(d["comparison"])
        recs.append(d)
    out = readiness(recs, halted=bool(ctl.get("halted")),
                    profitability=profitability)
    out["since"] = since
    out["cutover"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                      for k, v in cut.items() if k != "evidence"}
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

async def canonical_decision(conn, *, did, strategy, version, cand, side, sized,
                           ent, obs, md, econ, p, best_edge, verdict, refusals,
                           policy_decision, at, label, book_age, cfg, params,
                           pin) -> dict | None:
    """THE DECISION HOOK (decision_hooks.CANONICAL_DECISION): BUILD AND
    RECORD THE ONE CANONICAL DECISION INTENT of an ENTER
    decision (live_parity.build_decision_intent), with the agent components
    computed at the decision instant (canonical_components). Returns the
    intent, or None when it could not be built or recorded (the paper
    sibling then proceeds exactly as before, and no live proposal exists)."""
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
                                     wire=sized.get("wire"), now=at)
        pinnacle = cand.get("pinnacle") or {}
        intent = build_decision_intent(
            decision_id=did, strategy=strategy, strategy_version=version,
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
                if isinstance(params, dict) else None},
            opportunity_score=comps["opportunity_score"],
            derek=CC.derek_component(
                verdict=verdict, policy_version=version,
                policy_decision=policy_decision, refusals=refusals,
                economics=e, gross_edge_pp=best_edge, probability=p),
            karen=comps["karen"], allie=comps["allie"], eddie=comps["eddie"],
            us_market_slug=cand["us_market_slug"],
            contract={"us_market_slug": cand.get("us_market_slug"),
                      "fixture": cand.get("fixture"),
                      "condition_id": cand.get("condition_id"),
                      "payout_event": cand.get("payout_event"),
                      "payout_is_complement": cand.get("payout_is_complement"),
                      "sport_family": cand.get("sport_family"),
                      "event_key": (label or {}).get("event_key")},
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
        # R30C · THE OPPORTUNITY SCORE SHADOW TOURNAMENT: V1 (the score the
        # intent carries) and V2 (the lower-confidence-bound score) recorded
        # BESIDE the intent at this decision instant (migration 233). V2 has
        # no authority: it is not in the intent, nothing reads it to decide,
        # and a failure here never touches the decision (savepoint inside).
        from . import opportunity_tournament as OT
        await OT.record_entry(conn, intent=intent,
                              v1=comps.get("opportunity_score"),
                              v2=comps.get("opportunity_score_v2"))
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


async def production_cutover(conn) -> dict | None:
    """The recorded R30 production cutover, or None."""
    try:
        r = await conn.fetchrow("SELECT * FROM live_parity_cutover WHERE id = 1")
    except Exception:                                         # noqa: BLE001
        return None
    return None if r is None else dict(r)


async def cutover_checks(conn, *, release_sha: str, api_sha: str | None,
                         hooks_here: list) -> dict:
    """EVERY CUTOVER CONDITION, read from production now (pure reads). Each
    check is {passed, value}; the cutover is recorded only if all pass."""
    checks: dict = {}

    def put(name, passed, value=None):
        checks[name] = {"passed": bool(passed), "value": value}

    full = bool(release_sha) and len(release_sha) == 40 and all(
        c in "0123456789abcdef" for c in release_sha)
    put("RELEASE_SHA_IS_A_FULL_SHA", full, release_sha)
    put("API_RUNS_THE_RELEASE_SHA", api_sha == release_sha, api_sha)
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
    # THE READBACK: every R30 object present and readable
    objs = ["canonical_decision_intents", "canonical_management_intents",
            "canonical_intent_executions", "live_parity_ledger",
            "small_live_control", "small_live_order_events",
            "live_parity_hook_installs", "agent_work_requests"]
    present = {o: bool(await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", o)) for o in objs}
    div = await conn.fetchval(
        "SELECT count(*) FROM live_parity_ledger "
        " WHERE parity_state = 'LOGIC_DIVERGENCE'")
    put("READBACK_OBJECTS_PRESENT", all(present.values()), present)
    put("READBACK_NO_LOGIC_DIVERGENCE", div == 0, div)
    return {"checks": checks,
            "passed": all(c["passed"] for c in checks.values()),
            "install_id": None if inst is None else inst["install_id"],
            "workers_sha": wsha,
            "migrations": sorted(m for m in applied if m >= "225")}


async def record_cutover(conn, *, release_sha: str, recorded_by: str,
                         api_sha: str | None = None,
                         hooks_here: list | None = None) -> dict:
    """RECORD THE R30 PRODUCTION CUTOVER ONCE, only if every condition holds
    now. Returns {recorded, cutover, checks}. An existing cutover is returned
    unchanged (the table is append-only and singular)."""
    import os
    existing = await production_cutover(conn)
    if existing is not None:
        return {"recorded": False, "already": True, "cutover": existing}
    api = api_sha if api_sha is not None else os.environ.get("RENDER_GIT_COMMIT")
    hooks = installed_hooks() if hooks_here is None else hooks_here
    got = await cutover_checks(conn, release_sha=release_sha, api_sha=api,
                               hooks_here=hooks)
    if not got["passed"]:
        return {"recorded": False, "refused": [
            k for k, c in got["checks"].items() if not c["passed"]],
            "checks": got["checks"]}
    async with conn.transaction():
        await conn.execute(
            """INSERT INTO live_parity_cutover (cutover_at, release_sha,
                 api_sha, workers_sha, migrations, hook_install_id,
                 small_live_mode, small_live_halted, capital_activated,
                 evidence, recorded_by)
               VALUES (now(), $1, $2, $3, $4, $5, 'SHADOW', false, false,
                       $6::jsonb, $7) ON CONFLICT (id) DO NOTHING""",
            release_sha, api, got["workers_sha"], got["migrations"],
            got["install_id"], _j(got["checks"]), recorded_by)
    return {"recorded": True, "cutover": await production_cutover(conn),
            "checks": got["checks"]}


def uninstall() -> None:
    from . import decision_hooks as DH
    DH.CANONICAL_DECISION = None
    DH.CANONICAL_ENTRY_ADAPTERS = None
    DH.CANONICAL_MANAGEMENT_RECORD = None
    DH.CANONICAL_MANAGEMENT_ADAPTERS = None
