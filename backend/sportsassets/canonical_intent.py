"""THE CANONICAL INTENT (R30): pure builders, no I/O, no execution import.

The paper decision and management code (agents/paper_benchmark via the
decision hook, agents/paper_xavier directly) and the execution side
(live_parity) share THESE definitions, so the object both adapters consume is
built by one piece of code:

  build_decision_intent     the immutable canonical decision intent + sha256
  build_management_intent   the immutable canonical management intent + sha256
  management_action         the ONE action Xavier's review decides
  paper_entry_fields        what the PAPER adapter reads from the intent

This module imports nothing from the paper, order, venue, execution or
funded modules. The strategy -> sleeve map below is pinned equal to
bettor_paper_sleeves.STRATEGY_SLEEVE by a test.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal


INTENT_VERSION = "CANONICAL_DECISION_INTENT_V1"
MGMT_INTENT_VERSION = "CANONICAL_MANAGEMENT_INTENT_V1"
VENUE = "POLYMARKET"

INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED = (
    "INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
#: pinned equal to bettor_paper_sleeves.STRATEGY_SLEEVE by a test
STRATEGY_SLEEVE = {
    "PINNACLE_COMPLETED_GAME_PAPER": INVESTMENT,
    "DEREK_ENTRY_POLICY_V2": INVESTMENT,
    "PINNACLE_EXPLORATION_PAPER": TRAINING,
    "PINNACLE_ONLY_PAPER_BENCHMARK": BENCHMARK,
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER": BENCHMARK,
}

# management actions (migration 225)
ACT_EXIT = "SELL_EXIT"
ACT_REDUCE = "SELL_REDUCE"
ACT_CANCEL_FIRST = "CANCEL_PROTECTION_BEFORE_EXIT"
ACT_PROTECT = "MAINTAIN_STANDING_PROTECTION"
ACT_NONE = "NO_ORDER"

#: the venue's sell intent for a held side (pinned equal to
#: execmirror.EXIT_FOR by a test; stated here so this module imports no
#: execution module)
SELL_INTENT = {"LONG": "ORDER_INTENT_SELL_LONG",
               "SHORT": "ORDER_INTENT_SELL_SHORT"}


# ─────────────────────────── canonical form ───────────────────────────

def _norm(v):
    """Canonical JSON value: Decimals and floats as fixed strings, so the sha
    is identical whoever computes it."""
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v else None
    if isinstance(v, float):
        return format(Decimal(repr(v)).normalize(), "f")
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def canonical_json(obj: dict) -> str:
    return json.dumps(_norm(obj), sort_keys=True, separators=(",", ":"),
                      default=str)


def content_sha(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def decision_intent_id(decision_id: str) -> str:
    return "cdi_" + hashlib.sha256(("CDI:" + decision_id).encode()).hexdigest()[:24]


def management_intent_id(review_id: str) -> str:
    return "cmi_" + hashlib.sha256(("CMI:" + review_id).encode()).hexdigest()[:24]


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def sleeve_of(strategy) -> str:
    return STRATEGY_SLEEVE.get(str(strategy), UNCLASSIFIED)


def unavailable(why: str, **extra) -> dict:
    return dict({"status": "UNAVAILABLE", "why": why}, **extra)


# ─────────────────────────── the decision intent ───────────────────────

_INTENT_FIELDS = ("intent_version", "decision_id", "strategy", "strategy_version",
                  "sleeve", "evidence", "opportunity_score", "derek", "karen",
                  "allie", "eddie", "venue", "us_market_slug", "contract",
                  "holding_side", "order_intent", "order_type", "time_in_force",
                  "limit_price", "wire_price", "target_qty", "sizing_basis",
                  "created_at")


def build_decision_intent(*, decision_id: str, strategy: str,
                          strategy_version: str, evidence: dict,
                          opportunity_score: dict, derek: dict, karen: dict,
                          allie: dict, eddie: dict, us_market_slug: str,
                          contract: dict, holding_side: str, order_intent: str,
                          order_type: str, time_in_force: str, limit_price,
                          wire_price, target_qty, sizing_basis: dict,
                          created_at: float) -> dict:
    """THE ONE CANONICAL DECISION INTENT (pure). Every component is either a
    measured value or carries its reason; nothing is invented."""
    if not decision_id or not strategy or not strategy_version:
        raise ValueError("a canonical intent needs decision, strategy, version")
    if holding_side not in ("LONG", "SHORT"):
        raise ValueError("holding_side must be LONG or SHORT")
    body = {
        "intent_version": INTENT_VERSION, "decision_id": str(decision_id),
        "strategy": strategy, "strategy_version": strategy_version,
        "sleeve": sleeve_of(strategy), "evidence": evidence or {},
        "opportunity_score": opportunity_score or unavailable("NOT_COMPUTED"),
        "derek": derek, "karen": karen or {"state": "UNAVAILABLE"},
        "allie": allie or unavailable("NOT_COMPUTED"),
        "eddie": eddie or unavailable("NOT_COMPUTED"),
        "venue": VENUE, "us_market_slug": us_market_slug,
        "contract": contract or {}, "holding_side": holding_side,
        "order_intent": order_intent, "order_type": str(order_type),
        "time_in_force": str(time_in_force),
        "limit_price": _dec(limit_price), "wire_price": _dec(wire_price),
        "target_qty": _dec(target_qty), "sizing_basis": sizing_basis or {},
        "created_at": round(float(created_at), 3)}
    if body["target_qty"] is None or body["target_qty"] <= 0:
        raise ValueError("a canonical intent needs a positive target quantity")
    if body["wire_price"] is None or not (0 < body["wire_price"] < 1):
        raise ValueError("a canonical intent needs a wire price in (0, 1)")
    if "verdict" not in (derek or {}):
        raise ValueError("Derek's verdict is part of the intent")
    sha = content_sha(body)
    return dict(body, intent_id=decision_intent_id(decision_id),
                content_sha=sha)


def verify_intent(intent: dict) -> bool:
    """True when the intent's stored sha is the sha of its own content."""
    body = {k: intent.get(k) for k in _INTENT_FIELDS}
    for k in ("limit_price", "wire_price", "target_qty"):
        body[k] = _dec(body[k])
    if hasattr(body["created_at"], "timestamp"):
        body["created_at"] = body["created_at"].timestamp()
    body["created_at"] = round(float(body["created_at"]), 3)
    for k in ("evidence", "opportunity_score", "derek", "karen", "allie",
              "eddie", "contract", "sizing_basis"):
        if isinstance(body[k], str):
            body[k] = json.loads(body[k])
    return content_sha(body) == intent.get("content_sha")


# ─────────────────────────── the management intent ─────────────────────

_MGMT_FIELDS = ("intent_version", "review_id", "group_id", "position_key",
                "strategy", "sleeve", "valuation_id", "evidence_version",
                "evidence_state", "recommendation", "mechanical_selection",
                "action", "us_market_slug", "holding_side", "order_intent",
                "target_qty", "target_limit", "alternatives", "freshness",
                "reason", "created_at")


def management_action(*, chosen, fresh: bool, stale: bool, p_missing: bool,
                      protection_ok: bool, standing_live: bool,
                      candidate: dict | None, protective: dict | None,
                      open_qty) -> dict:
    """THE MANAGEMENT ACTION XAVIER'S REVIEW DECIDES (pure). This is the
    branch paper_xavier.review_group used to take inline; both adapters now
    consume its result through the canonical management intent.

    EXIT/REDUCE selected (only possible on fresh evidence -- the review strips
    discretionary sales otherwise): with a standing protection order working,
    cancel it first (its inventory is committed); else sell the walked
    quantity at the walk's worst price. HOLD, or nothing selectable on stale /
    absent evidence: keep exactly one standing protective sale at the
    protective price. Otherwise no order."""
    cand = candidate or {}
    walk = cand.get("walk") or {}
    if chosen in ("EXIT", "REDUCE"):
        if not fresh:
            # defensive: the review never selects a sale on stale evidence
            return {"action": ACT_NONE, "target_qty": None,
                    "target_limit": {"kind": "NONE",
                                     "why": "SALE_REFUSED_ON_NON_FRESH_EVIDENCE"}}
        if standing_live:
            return {"action": ACT_CANCEL_FIRST, "target_qty": None,
                    "target_limit": {"kind": "CANCEL_STANDING_PROTECTION",
                                     "why": "EXIT_WAITS_FOR_STANDING_TERMINAL"}}
        if not cand.get("qty") or walk.get("worst_price") is None:
            return {"action": ACT_NONE, "target_qty": None,
                    "target_limit": {"kind": "NONE", "why": "NO_WALKABLE_SALE"}}
        return {"action": ACT_EXIT if chosen == "EXIT" else ACT_REDUCE,
                "target_qty": _dec(cand["qty"]),
                "target_limit": {"kind": "BOOK_WALK_WORST_PRICE",
                                 "limit_price": _dec(walk.get("worst_price")),
                                 "wire_price": _dec(walk.get("worst_wire")),
                                 "order_type": "MARKETABLE",
                                 "time_in_force": "IOC"}}
    if (chosen == "HOLD" or (chosen is None and (not fresh or stale
                                                 or p_missing))) \
            and protection_ok and protective:
        return {"action": ACT_PROTECT, "target_qty": _dec(open_qty),
                "target_limit": {"kind": "PROTECTIVE_PRICE",
                                 "limit_price": _dec(protective.get("price")),
                                 "basis": protective.get("basis") or
                                 "cost recovery + fees + $0.01 per contract",
                                 "order_type": "RESTING",
                                 "time_in_force": "GTD"}}
    return {"action": ACT_NONE, "target_qty": None,
            "target_limit": {"kind": "NONE",
                             "why": "NO_ACTION_SELECTED" if chosen is None
                             else "SELECTED_%s_NEEDS_NO_ORDER" % chosen}}


def build_management_intent(*, review_id: str, group_id: str,
                            position_key: str, strategy, valuation: dict,
                            evidence_state: str, recommendation,
                            mechanical_selection, decided: dict,
                            us_market_slug: str, holding_side: str,
                            alternatives: dict, reason: dict,
                            created_at: float) -> dict:
    """THE ONE CANONICAL MANAGEMENT INTENT (pure)."""
    v = valuation or {}
    sell_intent = SELL_INTENT.get(holding_side)
    ranked = [{"action": c.get("action"), "qty": c.get("qty"),
               "expected_net_usd": c.get("expected_net_usd"),
               "ev_basis": c.get("ev_basis")}
              for c in (alternatives or {}).get("candidates") or []]
    blocked = [{"action": c.get("action"), "blocker": c.get("blocker")}
               for c in (alternatives or {}).get("not_rankable") or []]
    body = {
        "intent_version": MGMT_INTENT_VERSION, "review_id": review_id,
        "group_id": group_id, "position_key": str(position_key),
        "strategy": strategy, "sleeve": sleeve_of(strategy),
        "valuation_id": None if v.get("valuation_id") is None
        else str(v.get("valuation_id")),
        "evidence_version": v.get("valuation_hash"),
        "evidence_state": evidence_state, "recommendation": recommendation,
        "mechanical_selection": mechanical_selection,
        "action": decided["action"], "us_market_slug": us_market_slug,
        "holding_side": holding_side,
        "order_intent": sell_intent if decided["action"] in (
            ACT_EXIT, ACT_REDUCE, ACT_PROTECT) else None,
        "target_qty": _dec(decided.get("target_qty")),
        "target_limit": decided.get("target_limit") or {},
        "alternatives": {"ranked": ranked, "not_rankable": blocked},
        "freshness": {"evidence_state": evidence_state,
                      "source_at": v.get("source_at"),
                      "expires_at": v.get("expires_at"),
                      "limit_s": v.get("limit_s"),
                      "valuation_version": v.get("valuation_version")},
        "reason": reason or {}, "created_at": round(float(created_at), 3)}
    return dict(body, intent_id=management_intent_id(review_id),
                content_sha=content_sha(body))


def _management_body(intent: dict) -> dict:
    """The sha-covered content of a management intent, normalized from a
    stored row (jsonb as TEXT, numeric as Decimal, timestamptz) or a built
    dict -- the same normalization verify_intent applies to a decision
    intent. A jsonb column of this table always holds an OBJECT (or array)
    and a text column an identifier, enum, slug or sha, so a str value whose
    first character opens an object or array is the jsonb as asyncpg
    returned it; decoding by that rule keeps the verifier right when a field
    is added to _MGMT_FIELDS (a new jsonb field needs no second list)."""
    body = {k: intent.get(k) for k in _MGMT_FIELDS}
    body["target_qty"] = _dec(body.get("target_qty"))
    at = body.get("created_at")
    if hasattr(at, "timestamp"):
        at = at.timestamp()
    body["created_at"] = round(float(at), 3)
    for k, v in body.items():
        if isinstance(v, str) and v.lstrip()[:1] in ("{", "["):
            body[k] = json.loads(v)
    return body


def verify_management_intent(intent: dict) -> bool:
    """True when a management intent's stored sha is the sha of its own
    content (pure; the counterpart of verify_intent).

    THE GAP THIS CLOSES (R30A chaos review). The intent route served every
    management intent (`cmi_...`) with sha_verified=None: only decision
    intents had a production verifier, and the contract test for Xavier's
    review -> management intent checked the persisted row with a verifier
    written inside the test file. A reader therefore could not prove that
    the management intent both adapters consumed is the one recorded. The
    route now calls this on the persisted row; so does the contract test."""
    try:
        return content_sha(_management_body(intent)) == intent.get("content_sha")
    except (TypeError, ValueError):
        return False


def paper_entry_fields(intent: dict) -> dict:
    """WHAT THE PAPER ADAPTER READS FROM THE INTENT for the entry order. The
    simulator's own timing (eligible/expiry) and the account are added by the
    caller; side, quantity, prices and order form come from here only."""
    return {"holding_side": intent["holding_side"],
            "intent": intent["order_intent"],
            "us_market_slug": intent["us_market_slug"],
            "order_type": intent["order_type"],
            "time_in_force": intent["time_in_force"],
            "qty": intent["target_qty"], "limit_price": intent["limit_price"],
            "wire_price": intent["wire_price"],
            "decision_id": intent["decision_id"],
            "strategy": intent["strategy"]}
