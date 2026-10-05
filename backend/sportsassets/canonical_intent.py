"""THE CANONICAL INTENT (R30): pure builders, no I/O, no execution import.

The paper decision and management code (agents/paper_benchmark via the
decision hook, agents/paper_xavier directly) and the execution side
(live_parity) share THESE definitions, so the object both adapters consume is
built by one piece of code:

  build_decision_intent     the immutable canonical decision intent + sha256
  build_management_intent   the immutable canonical management intent + sha256
  management_action         the ONE action Xavier's review decides
  management_alternatives   EVERY management alternative, valued or with its
                            reason (R30A section 8)
  paper_entry_fields        what the PAPER adapter reads from the intent
  opportunity_key           the stable opportunity id (fixture | market |
                            side | line | scope)
  decision_expiry           the decision validity window (R30A): the intent
                            may not be executed after it
  policy_block /            the policy the decision ran under, and whether
  live_policy_verdict       LIVE may act on it (R30A section 23: fail closed)

This module imports nothing from the paper, order, venue, execution or
funded modules. The strategy -> sleeve map below is pinned equal to
bettor_paper_sleeves.STRATEGY_SLEEVE by a test.

R30A (V2 of both intents). The R30 V1 decision intent carried the components
but NOT: a stable opportunity key, the policy parameter version and its sha,
the probability's source / version / stamps / age, the risk rails in force,
Allie's binding constraints, evidence references, the stage timestamps of the
latency chain, or any validity window -- so nothing stopped an adapter from
executing a decision long after the 30 s probability it was priced on had
died. V2 carries all of them INSIDE the sha-covered content: a field outside
the hash could be changed after the fact without the intent's sha noticing.
The V1 management intent ranked only the alternatives the paper review could
price and dropped the rest silently; V2 records all eight alternatives
(HOLD, SELL_EXIT, SELL_REDUCE, CANCEL_PROTECTION_BEFORE_EXIT,
MAINTAIN_STANDING_PROTECTION, INDIRECT_HEDGE, REALLOCATE, NO_ORDER), each
EVALUATED with its value or UNAVAILABLE with its reason, the chosen one and
why. Migration 225 (undeployed when V2 replaced V1) stores both.
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_FLOOR, Decimal


INTENT_VERSION = "CANONICAL_DECISION_INTENT_V2"
MGMT_INTENT_VERSION = "CANONICAL_MANAGEMENT_INTENT_V2"
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

#: THE EIGHT MANAGEMENT ALTERNATIVES EVERY REVIEW EVALUATES (R30A section 8).
#: Five are the order actions above; HOLD (keep the position, no order
#: change), INDIRECT_HEDGE (a sibling-contract hedge) and REALLOCATE (sell to
#: fund a better-ranked opportunity) are alternatives that are valued but are
#: not order actions of the management intent. Migration 225 CHECKs that the
#: stored alternative set names exactly these.
ALT_HOLD = "HOLD"
ALT_INDIRECT = "INDIRECT_HEDGE"
ALT_REALLOCATE = "REALLOCATE"
ALTERNATIVES = (ALT_HOLD, ACT_EXIT, ACT_REDUCE, ACT_CANCEL_FIRST, ACT_PROTECT,
                ALT_INDIRECT, ALT_REALLOCATE, ACT_NONE)
EVALUATED, UNAVAILABLE = "EVALUATED", "UNAVAILABLE"

# decision-intent validity (R30A)
R_INTENT_EXPIRED = "CANONICAL_INTENT_EXPIRED"
R_EXPIRY_UNAVAILABLE = "CANONICAL_INTENT_EXPIRY_UNAVAILABLE"

# the policy a decision ran under (R30A section 23)
POLICY_ACTIVE = "ACTIVE_VERSION"              # paper_benchmark.P_ACTIVE
POLICY_FALLBACK = "SHIPPED_DEFAULT_FALLBACK"  # paper_benchmark.P_FALLBACK
POLICY_CODE_CONSTANT = "CODE_CONSTANT_POLICY_NO_PARAMETER_ROW"
LIVE_POLICY_RULE = "LIVE_POLICY_FAIL_CLOSED_V1"
R_POLICY_MISSING = "LIVE_POLICY_ROW_MISSING_OR_UNREADABLE"
R_POLICY_SHA = "LIVE_POLICY_SHA_MISMATCH"
R_POLICY_UNAPPROVED = "LIVE_POLICY_VERSION_NOT_APPROVED"
#: every LIVE policy refusal (the parity ledger treats them as governance,
#: not logic: live_parity.GOVERNANCE_EXCLUSIONS)
LIVE_POLICY_REFUSALS = (R_POLICY_MISSING, R_POLICY_SHA, R_POLICY_UNAPPROVED)
#: actors that are never a human approver (migration 225 CHECKs the SAME
#: pattern, character for character, on every approval / cutover row and on
#: a halt clear -- as redefined by migration 266, which adds 'archer' beside
#: 'eddie' when the execution agent was renamed; a test pins the two equal). Three parts, searched
#: case-insensitively anywhere in the actor:
#:   1 an agent / system identity at the START (the R30 list: these are also
#:     human first names, so only the leading position is refused, as before)
#:   2 a MACHINE word anywhere, as a whole word (delimited by a non-letter /
#:     non-digit or the ends): bot, ci, cron, codex, assistant, openai, gpt,
#:     automation, service, root, admin, scheduler, deploy, github, actions,
#:     worker, daemon, pipeline, webhook, script ... and the placeholders
#:     unknown / anonymous / none / null, which name nobody
#:   3 a GitHub App suffix `[bot]` (github-actions[bot], dependabot[bot])
#: R30A review: the first version was part 1 alone, anchored at the start,
#: so 'github-actions[bot]', 'ci', 'cron', 'codex', 'openai', 'root' and
#: 'admin' all passed as named humans -- for the cutover's recorded_by and
#: for live_approvals.approved_by, the LIVE governance approver.
NON_HUMAN_ACTOR_PATTERN = (
    r"^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|"
    r"archer|scout|bettor|claude|agent|migration|test_harness_system)"
    r"|(^|[^a-z0-9])(bots?|ci|cron|codex|assistant|openai|gpt|chatgpt|"
    r"anthropic|llm|copilot|automation|automated|service|svc|root|admin|"
    r"administrator|scheduler|deploy|deployer|render|github|actions|worker|"
    r"daemon|robot|script|pipeline|webhook|unknown|anonymous|none|null)"
    r"([^a-z0-9]|$)"
    r"|\[bot\]")

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


def is_named_human(actor) -> bool:
    """A named human actor: non-blank and not a system / agent / migration
    identity (the same pattern migration 225 CHECKs, case-insensitive)."""
    import re
    a = str(actor or "").strip()
    # SEARCH, not match: parts 2 and 3 of the pattern may sit anywhere in the
    # actor (Postgres `!~*` searches the same way)
    return bool(a) and re.search(NON_HUMAN_ACTOR_PATTERN, a, re.I) is None


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# ─────────────────────────── opportunity, expiry, policy (pure) ────────

def opportunity_key(*, fixture, us_market_slug, holding_side, line,
                    scope) -> str:
    """THE STABLE OPPORTUNITY ID: fixture | us_market_slug | holding_side |
    line | scope (the grading period). The same key the unique-opportunity
    funnel counts by: a re-evaluation of the same contract, side, line and
    scope is the SAME opportunity, never a new one. A missing part is the
    empty string (stated, never guessed); a numeric line is written in its
    normal decimal form so 3.5, 3.50 and Decimal('3.5') are one key."""
    def part(v):
        if v is None:
            return ""
        if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
            return format(Decimal(repr(v) if isinstance(v, float) else str(v))
                          .normalize(), "f")
        return str(v).replace("|", "/")
    return "|".join(part(x) for x in (fixture, us_market_slug, holding_side,
                                      line, scope))


def _floor_ms(stamp: float, limit_s: float) -> float:
    """stamp + limit, FLOORED to the millisecond (the precision the intent
    records). R30A review: this was round(stamp + limit, 3), which can land
    up to 0.5 ms AFTER the true end -- a sub-millisecond loosening of the
    30 s rule. Flooring can only end the window early, never late. Decimal
    arithmetic on the shortest repr of each float, so no binary rounding
    error moves the floor either way."""
    end = Decimal(repr(float(stamp))) + Decimal(repr(float(limit_s)))
    return float(end.quantize(Decimal("0.001"), rounding=ROUND_FLOOR))


def decision_expiry(*, probability_observed_at, probability_limit_s,
                    book_observed_at, book_max_age_s) -> dict:
    """THE DECISION VALIDITY WINDOW (pure), derived from the freshness rules
    the decision was admitted under -- never a new tolerance:

      probability   the Pinnacle source stamp + the 30 s rule the decision
                    re-aged it against (ext_pinnacle_loop.PINNACLE_MAX_AGE_S)
      book          our receipt instant of the executable book + the entry
                    rule's book age (paper_benchmark.BOOK_MAX_AGE_S)

    expires_at is the EARLIER: after it the decision's probability or its
    book is older than the rule that admitted it, so the intent may not be
    executed. A stamp that is missing makes the window UNDERIVABLE: expires_at
    is None and every adapter refuses (CANONICAL_INTENT_EXPIRY_UNAVAILABLE) --
    an intent whose validity cannot be shown is never treated as valid."""
    terms, missing = [], []
    for name, at, lim in (("PROBABILITY_FRESHNESS", probability_observed_at,
                           probability_limit_s),
                          ("BOOK_AGE", book_observed_at, book_max_age_s)):
        a, l_ = _epoch(at), _epoch(lim)
        if a is None or l_ is None:
            missing.append(name)
            terms.append({"term": name, "stamp": a, "limit_s": l_,
                          "expires_at": None})
            continue
        terms.append({"term": name, "stamp": round(a, 3), "limit_s": l_,
                      "expires_at": _floor_ms(a, l_)})
    if missing:
        return {"status": UNAVAILABLE, "expires_at": None,
                "why": "EXPIRY_UNDERIVABLE_MISSING:%s" % ",".join(missing),
                "terms": terms, "binding_term": None}
    first = min(terms, key=lambda t: t["expires_at"])
    return {"status": "DERIVED", "expires_at": first["expires_at"],
            "why": None, "terms": terms, "binding_term": first["term"],
            "rule": ("the earlier of the probability source stamp + its "
                     "freshness limit and the book receipt + the entry "
                     "rule's book age; never extended")}


def intent_expiry_refusal(intent: dict, *, now) -> str | None:
    """None when the intent may still be executed at `now`; otherwise the
    refusal. Every adapter (PAPER, SMALL LIVE, the ACTUAL sibling) calls
    this immediately before it would act on the intent."""
    exp = _epoch((intent or {}).get("expires_at"))
    if exp is None:
        return R_EXPIRY_UNAVAILABLE
    n = _epoch(now)
    if n is None or n > exp:
        return R_INTENT_EXPIRED
    return None


def params_row_sha(values) -> str | None:
    """The sha256 the parameter-version table stores for `params`
    (paper_learning writes sha256(json.dumps(params, sort_keys=True)); the
    migrations' literals are the same text)."""
    if not isinstance(values, dict):
        return None
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()
                          ).hexdigest()


def policy_block(*, strategy, strategy_version, params) -> dict:
    """THE POLICY A DECISION RAN UNDER (pure): the strategy version, the
    parameter version row read for it (paper_benchmark.cg_parameters), the
    row's stored sha256 against the sha of the values actually used, the
    approver, and `policy_sha` -- the sha256 of (strategy, strategy version,
    parameter version, values), which is what a LIVE approval names.

    PAPER MAY FALL BACK, ONLY EXPLICITLY AND LABELLED: when the active row was
    missing or unreadable the decision ran the shipped research default and
    this block says so (`fallback.explicit`, its reason, and
    `live_admissible: False`). LIVE never runs on a fallback
    (`live_policy_verdict`)."""
    p = params if isinstance(params, dict) else None
    if p is None:
        body = {"strategy": strategy, "strategy_version": strategy_version,
                "parameters_version_id": None, "values": None}
        return dict(body, parameters_source=POLICY_CODE_CONSTANT,
                    parameters_version_no=None, params_sha256=None,
                    params_sha256_recomputed=None, row_sha_matches=None,
                    approved_by=None, version_source=None,
                    activation_id=None, activation_kind=None,
                    fallback=None, policy_sha=content_sha(body))
    values = p.get("values")
    stored = p.get("params_sha256")
    recomputed = params_row_sha(values)
    src = p.get("source")
    body = {"strategy": strategy, "strategy_version": strategy_version,
            "parameters_version_id": p.get("version_id"), "values": values}
    fb = None
    if src != POLICY_ACTIVE:
        fb = {"explicit": True,
              "label": "PAPER_SHIPPED_RESEARCH_DEFAULT_FALLBACK",
              "reason": p.get("fallback_reason") or "NO_ACTIVE_PARAMETER_ROW",
              "live_admissible": False}
    return dict(body, parameters_source=src,
                parameters_version_no=p.get("version_no"),
                params_sha256=stored, params_sha256_recomputed=recomputed,
                row_sha_matches=(None if stored is None or recomputed is None
                                 else stored == recomputed),
                approved_by=p.get("approved_by"),
                version_source=p.get("version_source"),
                activation_id=p.get("activation_id"),
                activation_kind=p.get("activation_kind"),
                fallback=fb, policy_sha=content_sha(body))


def live_policy_verdict(policy: dict | None, *,
                        approved_policy_shas=frozenset()) -> dict:
    """MAY LIVE ACT ON THIS POLICY? (pure; R30A section 23, audit P0 #8).
    FAIL CLOSED, in order:

      1 the policy row is present and readable: the parameter version came
        from the ACTIVE row (not the shipped fallback, not a read failure,
        not a policy with no parameter row at all)
      2 its sha matches: the row's stored sha256 is the sha of the values
        the decision actually used
      3 its version is approved: the version row names a human approver AND
        an owner LIVE approval exists for exactly this `policy_sha` (strategy
        version + parameter version + values; live_approvals, migration 225).
        A paper-only authorization is not a LIVE approval.

    {admissible, refusal (the first failing check), checks}."""
    p = policy if isinstance(policy, dict) else {}
    checks = []

    def put(name, ok, code, **ev):
        checks.append(dict({"check": name, "passed": bool(ok),
                            "refusal": None if ok else code}, **ev))

    present = (bool(p) and p.get("parameters_source") == POLICY_ACTIVE
               and isinstance(p.get("values"), dict) and not p.get("fallback"))
    put("policy_row_present_and_readable", present, R_POLICY_MISSING,
        parameters_source=p.get("parameters_source"),
        fallback=(p.get("fallback") or {}).get("reason"))
    put("policy_row_sha_matches", p.get("row_sha_matches") is True,
        R_POLICY_SHA, stored=p.get("params_sha256"),
        recomputed=p.get("params_sha256_recomputed"))
    approved = (is_named_human(p.get("approved_by"))
                and p.get("policy_sha") in set(approved_policy_shas or ()))
    put("policy_version_approved_for_live", approved, R_POLICY_UNAPPROVED,
        approved_by=p.get("approved_by"), policy_sha=p.get("policy_sha"),
        live_approval_present=p.get("policy_sha") in set(
            approved_policy_shas or ()))
    failed = [c for c in checks if not c["passed"]]
    return {"rule": LIVE_POLICY_RULE, "admissible": not failed,
            "refusal": failed[0]["refusal"] if failed else None,
            "refusals": [c["refusal"] for c in failed], "checks": checks}


# ─────────────────────────── the decision intent ───────────────────────

_INTENT_FIELDS = ("intent_version", "decision_id", "opportunity_id", "strategy",
                  "strategy_version", "policy", "sleeve", "evidence",
                  "probability", "book", "risk_rails", "binding_constraints",
                  "evidence_refs", "latency_stages", "opportunity_score",
                  "derek", "karen", "allie", "eddie", "venue",
                  "us_market_slug", "contract", "holding_side",
                  "order_intent", "order_type", "time_in_force",
                  "limit_price", "wire_price", "target_qty", "sizing_basis",
                  "created_at", "expires_at", "expiry")
_JSON_FIELDS = ("policy", "evidence", "probability", "book", "risk_rails",
                "binding_constraints", "evidence_refs", "latency_stages",
                "opportunity_score", "derek", "karen", "allie", "eddie",
                "contract", "sizing_basis", "expiry")


def binding_constraints(allie: dict | None, sizing_basis: dict | None) -> dict:
    """THE CONSTRAINTS THAT BOUND THIS DECISION (pure): Allie's binding term
    and final binding term (allie_capital: the term that set the proposed
    allocation, and HARD_RISK_RAIL when the rail cut it), and the sizing
    stop that bounded the order. Unmeasured is stated, never filled."""
    a = allie if isinstance(allie, dict) else {}
    s = sizing_basis if isinstance(sizing_basis, dict) else {}
    return {"allie_binding_constraint": a.get("binding_constraint")
            if a else None,
            "allie_final_binding": a.get("final_binding") if a else None,
            "allie_status": a.get("status") if a else "UNAVAILABLE",
            "allie_why": (a.get("why") if a else "NO_ALLIE_COMPONENT"),
            "sizing_fee_stop": s.get("fee_stop"),
            "sizing_depth_within_limit": s.get("depth_within_limit"),
            "sizing_rule": s.get("rule"),
            "authority": ("Allie's allocation is SHADOW_PENDING_OWNER_APPROVAL:"
                          " recorded, it does not resize the order")}


def build_decision_intent(*, decision_id: str, strategy: str,
                          strategy_version: str, evidence: dict,
                          opportunity_score: dict, derek: dict, karen: dict,
                          allie: dict, eddie: dict, us_market_slug: str,
                          contract: dict, holding_side: str, order_intent: str,
                          order_type: str, time_in_force: str, limit_price,
                          wire_price, target_qty, sizing_basis: dict,
                          created_at: float, opportunity_id: str | None = None,
                          policy: dict | None = None,
                          probability: dict | None = None,
                          book: dict | None = None,
                          risk_rails: dict | None = None,
                          evidence_refs: list | None = None,
                          latency_stages: dict | None = None,
                          expiry: dict | None = None) -> dict:
    """THE ONE CANONICAL DECISION INTENT (pure). Every component is either a
    measured value or carries its reason; nothing is invented. Everything
    below is inside the sha (`content_sha`), expires_at included."""
    if not decision_id or not strategy or not strategy_version:
        raise ValueError("a canonical intent needs decision, strategy, version")
    if holding_side not in ("LONG", "SHORT"):
        raise ValueError("holding_side must be LONG or SHORT")
    c = contract or {}
    if opportunity_id is None:
        opportunity_id = opportunity_key(
            fixture=c.get("fixture"), us_market_slug=us_market_slug,
            holding_side=holding_side, line=c.get("line"),
            scope=c.get("scope"))
    ev = evidence or {}
    if expiry is None:
        expiry = decision_expiry(
            probability_observed_at=(probability or {}).get("observed_at"),
            probability_limit_s=(probability or {}).get("limit_s"),
            book_observed_at=(book or {}).get("observed_at"),
            book_max_age_s=(book or {}).get("max_age_s"))
    allie_c = allie or unavailable("NOT_COMPUTED")
    body = {
        "intent_version": INTENT_VERSION, "decision_id": str(decision_id),
        "opportunity_id": str(opportunity_id),
        "strategy": strategy, "strategy_version": strategy_version,
        "policy": policy or policy_block(strategy=strategy,
                                         strategy_version=strategy_version,
                                         params=None),
        "sleeve": sleeve_of(strategy), "evidence": ev,
        "probability": probability or unavailable("NOT_RECORDED"),
        "book": book or unavailable("NOT_RECORDED"),
        "risk_rails": risk_rails or unavailable("NOT_RECORDED"),
        "binding_constraints": binding_constraints(allie, sizing_basis),
        "evidence_refs": list(evidence_refs or []),
        "latency_stages": latency_stages or {},
        "opportunity_score": opportunity_score or unavailable("NOT_COMPUTED"),
        "derek": derek, "karen": karen or {"state": "UNAVAILABLE"},
        "allie": allie_c,
        "eddie": eddie or unavailable("NOT_COMPUTED"),
        "venue": VENUE, "us_market_slug": us_market_slug,
        "contract": c, "holding_side": holding_side,
        "order_intent": order_intent, "order_type": str(order_type),
        "time_in_force": str(time_in_force),
        "limit_price": _dec(limit_price), "wire_price": _dec(wire_price),
        "target_qty": _dec(target_qty), "sizing_basis": sizing_basis or {},
        "created_at": round(float(created_at), 3),
        "expires_at": expiry.get("expires_at"),
        "expiry": expiry}
    if body["target_qty"] is None or body["target_qty"] <= 0:
        raise ValueError("a canonical intent needs a positive target quantity")
    if body["wire_price"] is None or not (0 < body["wire_price"] < 1):
        raise ValueError("a canonical intent needs a wire price in (0, 1)")
    if "verdict" not in (derek or {}):
        raise ValueError("Derek's verdict is part of the intent")
    if body["opportunity_id"].count("|") != 4:
        raise ValueError("the opportunity id is fixture|slug|side|line|scope")
    sha = content_sha(body)
    return dict(body, intent_id=decision_intent_id(decision_id),
                content_sha=sha)


def _intent_body(intent: dict) -> dict:
    """The sha-covered content of a decision intent, normalized from a
    stored row (JSON text, datetimes, numerics) or a built dict."""
    body = {k: intent.get(k) for k in _INTENT_FIELDS}
    for k in ("limit_price", "wire_price", "target_qty"):
        body[k] = _dec(body[k])
    body["created_at"] = round(float(_epoch(body["created_at"])), 3)
    exp = _epoch(body["expires_at"])
    body["expires_at"] = None if exp is None else round(exp, 3)
    for k in _JSON_FIELDS:
        if isinstance(body[k], str):
            body[k] = json.loads(body[k])
    return body


def verify_intent(intent: dict) -> bool:
    """True when the intent's stored sha is the sha of its own content."""
    try:
        return content_sha(_intent_body(intent)) == intent.get("content_sha")
    except (TypeError, ValueError):
        return False


# ─────────────────────────── the management intent ─────────────────────

_MGMT_FIELDS = ("intent_version", "review_id", "group_id", "position_key",
                "strategy", "sleeve", "valuation_id", "evidence_version",
                "evidence_state", "recommendation", "mechanical_selection",
                "action", "us_market_slug", "holding_side", "order_intent",
                "target_qty", "target_limit", "alternatives",
                "alternative_set", "chosen", "chosen_why", "policy",
                "freshness", "reason", "created_at")


#: WAS THE ALTERNATIVE'S EVALUATION RUN? (R30A review). UNAVAILABLE has two
#: meanings that parity must not conflate: the review RAN the evaluation and
#: found the alternative unavailable (no standing protection to cancel, no
#: bids, a stale measure) -- an evaluated fact -- or the evaluation was NOT
#: RUN at all (the paper book runs no indirect-hedge search; a reallocation
#: not compared; a value never computed): the comparison is INCOMPLETE on it,
#: and exact parity may not be claimed while any alternative is NOT_RUN.
RAN, NOT_RUN = "RAN", "NOT_RUN"


def _alt(name, status, *, why=None, evaluation=RAN, **values) -> dict:
    out = {"alternative": name, "status": status}
    if status == UNAVAILABLE:
        out["why"] = why or "NOT_EVALUATED"
        out["evaluation"] = evaluation
    else:
        out["evaluation"] = RAN
    out.update({k: v for k, v in values.items()})
    return out


def _not_run_reason(why) -> bool:
    w = str(why or "")
    return ("NOT_SEARCHED" in w or "SEARCH_NOT_RUN" in w
            or w.endswith("_NOT_VALUED") or w.endswith("_NOT_COMPARED")
            or w == "NOT_EVALUATED")


def management_alternatives(*, alts: dict | None, decided: dict,
                            mechanical_selection, standing_live: bool,
                            protective: dict | None, open_qty,
                            reallocate: dict | None) -> dict:
    """EVERY MANAGEMENT ALTERNATIVE OF ONE REVIEW, EACH EVALUATED WITH ITS
    VALUE OR UNAVAILABLE WITH ITS REASON (pure; R30A section 8). Read from
    what the review already computed -- its ranking (paper_xavier.
    alternatives, after the stale-evidence strip), the protective price, the
    standing order and Xavier's REALLOCATE comparison -- so nothing here
    re-values or re-decides anything:

      HOLD                          q x p on the review's measure (its
                                    evidence basis stated; on stale evidence
                                    the current value is null and the
                                    entry-time value is labelled)
      SELL_EXIT / SELL_REDUCE       the walked sale's value, or the blocker
                                    (stale measure, no bids, nothing to sell)
      CANCEL_PROTECTION_BEFORE_EXIT the first step of a sale while a standing
                                    protection commits the inventory: valued
                                    as the best walkable sale it precedes;
                                    UNAVAILABLE with no standing protection
      MAINTAIN_STANDING_PROTECTION  the protective floor (NOT realized)
      INDIRECT_HEDGE                the paper book runs no sibling-contract
                                    search: UNAVAILABLE, the comparison is
                                    incomplete on it -- never assumed empty
      REALLOCATE                    Xavier's shadow comparison of the
                                    position's capital efficiency against the
                                    best qualified opportunity
      NO_ORDER                      the position as-is, no order: the HOLD
                                    value (no protection placed)

    Returns {alternative: entry} for all eight, and `chosen` is marked on the
    one the management action took."""
    a = alts if isinstance(alts, dict) else {}
    cands = {c.get("action"): c for c in a.get("candidates") or []}
    blocked = {}
    for c in a.get("not_rankable") or []:
        blocked.setdefault(c.get("action"), c)
    out: dict = {}
    hold = cands.get("HOLD")
    if hold is not None:
        out[ALT_HOLD] = _alt(ALT_HOLD, EVALUATED,
                             value_usd=hold.get("value_usd"),
                             expected_net_usd=hold.get("expected_net_usd"),
                             entry_time_expected_net_usd=hold.get(
                                 "entry_time_expected_net_usd"),
                             ev_basis=hold.get("ev_basis"),
                             ev_is_current=hold.get("ev_is_current"),
                             qty=hold.get("qty"))
    else:
        why = (blocked.get("HOLD") or {}).get("blocker") or "HOLD_NOT_VALUED"
        out[ALT_HOLD] = _alt(ALT_HOLD, UNAVAILABLE, why=why,
                             evaluation=NOT_RUN if _not_run_reason(why)
                             else RAN)
    sales = {}
    for name, key in ((ACT_EXIT, "EXIT"), (ACT_REDUCE, "REDUCE")):
        c = cands.get(key)
        if c is not None:
            out[name] = _alt(name, EVALUATED, value_usd=c.get("value_usd"),
                             expected_net_usd=c.get("expected_net_usd"),
                             qty=c.get("qty"), fees_usd=c.get("fees_usd"),
                             worst_price=(c.get("walk") or {}).get(
                                 "worst_price"))
            sales[name] = c
        else:
            why = (blocked.get(key) or {}).get("blocker") or "SALE_NOT_VALUED"
            out[name] = _alt(name, UNAVAILABLE, why=why,
                             evaluation=NOT_RUN if _not_run_reason(why)
                             else RAN)
    if not standing_live:
        out[ACT_CANCEL_FIRST] = _alt(ACT_CANCEL_FIRST, UNAVAILABLE,
                                     why="NO_STANDING_PROTECTION_TO_CANCEL")
    elif sales:
        best = max(sales.values(), key=lambda c: float(
            c.get("value_usd") if c.get("value_usd") is not None else -1e18))
        out[ACT_CANCEL_FIRST] = _alt(
            ACT_CANCEL_FIRST, EVALUATED, value_usd=best.get("value_usd"),
            precedes=best.get("action"),
            basis=("the first step of the walkable sale it precedes: the "
                   "standing protection commits the inventory"))
    else:
        out[ACT_CANCEL_FIRST] = _alt(
            ACT_CANCEL_FIRST, UNAVAILABLE,
            why="NO_WALKABLE_SALE_TO_CANCEL_FOR:%s" % (
                out[ACT_EXIT].get("why") or "SALE_NOT_VALUED"))
    pr = protective if isinstance(protective, dict) else {}
    if pr.get("ok"):
        out[ACT_PROTECT] = _alt(ACT_PROTECT, EVALUATED,
                                protective_price=pr.get("price"),
                                floor_usd=pr.get("floor_usd"),
                                qty=_dec(open_qty), floor_is_realized=False,
                                basis=pr.get("floor_is"))
    else:
        # no protective computation at all is NOT_RUN; a computed refusal ran
        out[ACT_PROTECT] = _alt(ACT_PROTECT, UNAVAILABLE,
                                why=pr.get("refusal") or "NO_PROTECTIVE_PRICE",
                                evaluation=RAN if pr else NOT_RUN)
    ind_c = cands.get("ACQUIRE_INDIRECT_HEDGE")
    if ind_c is not None:
        out[ALT_INDIRECT] = _alt(ALT_INDIRECT, EVALUATED,
                                 value_usd=ind_c.get("value_usd"),
                                 expected_net_usd=ind_c.get(
                                     "expected_net_usd"))
    else:
        why = ((blocked.get("ACQUIRE_INDIRECT_HEDGE") or {}).get("blocker")
               or (a.get("incomplete_search") or {}).get("why")
               or "INDIRECT_HEDGE_NOT_SEARCHED")
        search = a.get("incomplete_search")
        not_run = (_not_run_reason(why) or (isinstance(search, dict)
                                            and search.get("complete") is False))
        out[ALT_INDIRECT] = _alt(ALT_INDIRECT, UNAVAILABLE, why=why,
                                 evaluation=NOT_RUN if not_run else RAN)
    r = reallocate if isinstance(reallocate, dict) else None
    if r is None:
        out[ALT_REALLOCATE] = _alt(ALT_REALLOCATE, UNAVAILABLE,
                                   why="REALLOCATE_NOT_COMPARED",
                                   evaluation=NOT_RUN)
    elif r.get("position_efficiency") is not None and \
            r.get("alternative_efficiency") is not None:
        out[ALT_REALLOCATE] = _alt(
            ALT_REALLOCATE, EVALUATED, mode="SHADOW",
            recommended=bool(r.get("recommended")),
            position_efficiency=r.get("position_efficiency"),
            alternative_efficiency=r.get("alternative_efficiency"),
            advantage=r.get("advantage"),
            value_usd=r.get("position_ev_from_here_usd"),
            best_opportunity=(r.get("best_opportunity") or {}).get(
                "decision_id"))
    else:
        out[ALT_REALLOCATE] = _alt(ALT_REALLOCATE, UNAVAILABLE, mode="SHADOW",
                                   why=r.get("blocker")
                                   or "REALLOCATE_NOT_COMPARABLE")
    if hold is not None:
        out[ACT_NONE] = _alt(ACT_NONE, EVALUATED,
                             value_usd=hold.get("value_usd"),
                             expected_net_usd=hold.get("expected_net_usd"),
                             basis="the position held as-is; no order placed")
    else:
        out[ACT_NONE] = _alt(ACT_NONE, UNAVAILABLE,
                             why=out[ALT_HOLD].get("why"),
                             evaluation=out[ALT_HOLD]["evaluation"])
    chosen = decided.get("action")
    for k, v in out.items():
        v["chosen"] = (k == chosen)
    if mechanical_selection == "HOLD":
        out[ALT_HOLD]["mechanical_selection"] = True
    return out


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


def chosen_why(*, decided: dict, mechanical_selection, reason: dict | None,
               evidence_state) -> dict:
    """WHY THE CHOSEN ACTION (pure): the selector's own reason and margin,
    and the management-action branch that turned the selection into this
    action (management_action's target_limit names it)."""
    r = reason if isinstance(reason, dict) else {}
    tl = decided.get("target_limit") or {}
    return {"action": decided.get("action"),
            "mechanical_selection": mechanical_selection,
            "selection_reason": r.get("selection_reason"),
            "selector_refusal": r.get("refusal"),
            "margin_over_runner_up": r.get("margin_over_runner_up"),
            "action_rule": tl.get("why") or tl.get("kind"),
            "evidence_state": evidence_state,
            "rule": ("management_action: EXIT/REDUCE only on fresh evidence "
                     "(cancel a standing protection first); HOLD or nothing "
                     "selectable on stale/absent evidence keeps one standing "
                     "protective sale; otherwise no order")}


def build_management_intent(*, review_id: str, group_id: str,
                            position_key: str, strategy, valuation: dict,
                            evidence_state: str, recommendation,
                            mechanical_selection, decided: dict,
                            us_market_slug: str, holding_side: str,
                            alternatives: dict, reason: dict,
                            created_at: float,
                            alternative_set: dict | None = None,
                            policy: dict | None = None) -> dict:
    """THE ONE CANONICAL MANAGEMENT INTENT (pure). `alternative_set` is
    management_alternatives(...): all eight alternatives; when a caller has
    not computed it, the set is built from the ranking alone (REALLOCATE
    then UNAVAILABLE: not compared) -- never omitted."""
    v = valuation or {}
    sell_intent = SELL_INTENT.get(holding_side)
    ranked = [{"action": c.get("action"), "qty": c.get("qty"),
               "expected_net_usd": c.get("expected_net_usd"),
               "ev_basis": c.get("ev_basis")}
              for c in (alternatives or {}).get("candidates") or []]
    blocked = [{"action": c.get("action"), "blocker": c.get("blocker")}
               for c in (alternatives or {}).get("not_rankable") or []]
    if alternative_set is None:
        alternative_set = management_alternatives(
            alts=alternatives, decided=decided,
            mechanical_selection=mechanical_selection, standing_live=(
                decided.get("action") == ACT_CANCEL_FIRST),
            protective=None, open_qty=decided.get("target_qty"),
            reallocate=None)
    if set(alternative_set) != set(ALTERNATIVES):
        raise ValueError("the alternative set must name all eight")
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
        "alternative_set": alternative_set,
        "chosen": decided["action"],
        "chosen_why": chosen_why(decided=decided,
                                 mechanical_selection=mechanical_selection,
                                 reason=reason, evidence_state=evidence_state),
        "policy": policy or {"status": "UNAVAILABLE",
                             "why": "MANAGEMENT_POLICY_NOT_RECORDED"},
        "freshness": {"evidence_state": evidence_state,
                      "source_at": v.get("source_at"),
                      "expires_at": v.get("expires_at"),
                      "limit_s": v.get("limit_s"),
                      "valuation_version": v.get("valuation_version")},
        "reason": reason or {}, "created_at": round(float(created_at), 3)}
    return dict(body, intent_id=management_intent_id(review_id),
                content_sha=content_sha(body))


def evaluated_set(alternative_set) -> dict:
    """{alternative: EVALUATED | UNAVAILABLE} of a stored or built set."""
    s = alternative_set
    if isinstance(s, str):
        s = json.loads(s)
    return {k: (v or {}).get("status") for k, v in (s or {}).items()}


def not_run_set(alternative_set) -> list:
    """The alternatives whose evaluation was NOT RUN (sorted). An UNAVAILABLE
    entry that does not say whether it ran counts as not run (fail closed:
    it can never support a claim of exact parity)."""
    s = alternative_set
    if isinstance(s, str):
        s = json.loads(s)
    return sorted(k for k, v in (s or {}).items()
                  if (v or {}).get("status") != EVALUATED
                  and (v or {}).get("evaluation") != RAN)

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
