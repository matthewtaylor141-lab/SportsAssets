"""ACTUAL-LANE ADMISSION: THE DECISION-TIME EVIDENCE A REAL ORDER REQUIRES.

Capital-critical (independent audit of ebe54a8, 2026-10-03). A V3 intent could
be marked live_eligible = true from its STRATEGY and POLICY VERSION alone while
its own decision record said book currency NOT_ESTABLISHED and settlement
compatibility UNKNOWN; only the emergency stop kept it from the venue.

This module is the ONE rule that turns a qualified paper decision's recorded
FACTS into an actual-lane admission. It is pure (no I/O, no imports from the
venue or the paper modules) so the same function runs twice:

  1. when the immutable execution intent is written (`execution_intent.create`):
     an inadmissible intent is REFUSED there, live_eligible = false, with the
     first failing requirement as its refusal and every requirement's result
     in `live_eligibility.admission`;
  2. again inside `ActualLane._run`, from the facts stored ON the intent,
     immediately before the claim and before Venue.place -- an intent row is
     never trusted for a boolean it carries.

PAPER IS UNTOUCHED. The completed-game paper policy may still decide on a
contract whose exceptional settlement terms are a DISCLOSED research risk;
that disclosure is evidence for paper research and is NEVER settlement
compatibility here. PinnAPI stays the sole probability authority
(outcome_books = 1 is valid); these requirements concern EXECUTION and
SETTLEMENT evidence, not probability corroboration.

FAIL CLOSED: every requirement passes only on an explicit positive fact. A
missing, null, NOT_ESTABLISHED, STALE, GAP, REFUSED or UNKNOWN value never
becomes admissible.

The runtime requirements that depend on the moment of submission (the switch,
the account fingerprint, buying power, venue-valid quantity, the $25 cap, the
decision's expiry, the executable book's age and idempotency) are checked by
`ActualLane._run` with the codes in `execution_intent`; `RUNTIME` names them
so the admission record lists all seventeen.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "ACTUAL_ADMISSION_V1"
LIVE_ADMISSIBLE = "LIVE_ADMISSIBLE"
NOT_ADMISSIBLE = "NOT_ADMISSIBLE"

#: THE LIVE BOOK-CURRENCY RULES MANAGEMENT HAS APPROVED. A book is live-current
#: only when its recorded currency verdict is ESTABLISHED under one of these
#: versioned rules. EMPTY: the venue documentation reviewed so far does not
#: establish a live-currentness rule (P5 stays NOT_ESTABLISHED), so no actual
#: order can be admitted. A new rule is added here only with its cited
#: evidence, exact semantics, tests and audit record -- never to enable trading.
#: Review of the current venue documentation (2026-10-03, verdict B, the gate
#: stays closed): research/p5_live_book_currency_review.md.
#: THIS CONSTANT STAYS EMPTY. The callers (execution_intent.create and
#: ActualLane._run) pass `approved_book_rules` = this constant UNION the rule
#: ids whose owner-approval artifact (live_rule_artifacts, migration 204) is
#: APPROVED with an owner record AND whose stored sha256 equals the deployed
#: code's rule hash (live_rule_artifacts.approved_live_book_rules; any read
#: failure -> this constant alone). This module stays pure.
APPROVED_LIVE_BOOK_RULES: frozenset = frozenset()

#: THE SETTLEMENT-COMPATIBILITY GATE AND ITS APPROVAL (R30A section 24). The
#: gate stays: settlement is admissible only on explicit positive facts
#: (`settlement_admission`). Its APPROVAL is now versioned and hash-matched
#: like the book-currency rule's: the gate admits only when its id is in the
#: approved set the caller passes, and the callers (execution_intent) pass
#: this code constant UNION the gates whose owner approval in live_approvals
#: names the sha of THIS gate's enforced configuration
#: (live_approvals.config_sha256: SETTLEMENT_GATE_RULE below, the version,
#: NEGATIVE). A changed rule changes that sha and the approval stops
#: admitting. EMPTY: no settlement gate approval exists, so nothing is
#: admitted -- an absent approval is a refusal, never a default pass.
SETTLEMENT_GATE_ID = "SETTLEMENT_COMPATIBILITY_V1"
SETTLEMENT_GATE_VERSION = "1"
APPROVED_SETTLEMENT_GATES: frozenset = frozenset()
#: what settlement_admission enforces (read by live_approvals.gate_config)
SETTLEMENT_GATE_RULE = {
    "compatibility_required": "COMPATIBLE",
    "overall_established_required": True,
    "blockers_allowed": 0,
    "lane_refusals_allowed": 0,
    "research_disclosure_counts": False}

#: The PinnAPI probability authority basis the V3 investment policy records.
PINNAPI_AUTHORITY = "PINNAPI_SOLE_PROBABILITY_AUTHORITY"

#: Venue market states that are explicitly tradable. Anything else -- absent,
#: halted, suspended, closed, resolved, unknown -- is not.
#: INSTRUMENT_STATE_OPEN is the exchange's own explicit OPEN state, carried by
#: a book priced from the institutional stream (P5 C12: the state comes from
#: the same observation as the price); it is the only state P5's C10 accepts.
TRADABLE_MARKET_STATES = frozenset({
    "MARKET_STATE_OPEN", "MARKET_STATUS_OPEN", "OPEN",
    "INSTRUMENT_STATE_OPEN"})

#: The identity checks the decision's contract match must have PASSED.
REQUIRED_IDENTITY_CHECKS = ("fixture_participants_date_side_period",
                            "payout_outcome_match")
#: The checks that bind the executable venue contract.
REQUIRED_CONTRACT_CHECKS = ("polymarket_us_contract",)

#: Values that must never be read as a positive fact.
NEGATIVE = frozenset({None, "", "NOT_ESTABLISHED", "STALE", "GAP", "REFUSED",
                      "UNKNOWN", "UNAVAILABLE", "NONE"})

# The seventeen requirements, in order (codes are the refusals).
R_PROBABILITY = "ADMISSION_PINNAPI_EVIDENCE_NOT_FRESH_OR_NOT_MAPPED"
R_IDENTITY = "ADMISSION_IDENTITY_NOT_EXACT"
R_CONTRACT = "ADMISSION_VENUE_CONTRACT_NOT_ESTABLISHED"
R_BOOK_CURRENCY = "ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE"
R_TRADABLE = "ADMISSION_MARKET_NOT_TRADABLE"
R_PRICE = "ADMISSION_EXECUTABLE_PRICE_UNKNOWN"
R_DEPTH = "ADMISSION_DEPTH_NOT_AVAILABLE"
R_FEES = "ADMISSION_FEES_UNKNOWN"
R_SLIPPAGE = "ADMISSION_SLIPPAGE_NOT_REPRESENTED"
R_NET_EV = "ADMISSION_NET_EV_NOT_POSITIVE"
R_SETTLEMENT = "ADMISSION_SETTLEMENT_NOT_LIVE_ADMISSIBLE"
R_FACTS_ABSENT = "ADMISSION_DECISION_FACTS_ABSENT"

RUNTIME = (
    ("account_identity", "RETAIL_ACCOUNT_FINGERPRINT_CHANGED"),
    ("buying_power", "INSUFFICIENT_CASH / ACCOUNT_STATE_NOT_CURRENT"),
    ("venue_valid_quantity", "BELOW_VENUE_MINIMUM"),
    ("order_cap_usd", "ABOVE_ORDER_CAP"),
    ("decision_not_expired", "DECISION_STALE_AT_ACTUAL_SUBMIT"),
    ("idempotency", "INTENT_ALREADY_CLAIMED"))


def _pos(v) -> bool:
    return v not in NEGATIVE and not (isinstance(v, str)
                                      and v.upper() in NEGATIVE)


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _passed(checks, name) -> bool:
    for c in checks or []:
        if isinstance(c, dict) and c.get("check") == name:
            return c.get("passed") is True
    return False


def facts_digest(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True,
                                     default=str).encode()).hexdigest()


def settlement_admission(st: dict | None, *, approved=None) -> dict:
    """LIVE_ADMISSIBLE only when the venue and book settlement terms were
    compared COMPATIBLE, every rule established, no blocker and no
    settlement-stage lane refusal is recorded -- AND the settlement gate's
    configuration approval is in force (`approved`: the approved gate ids;
    None -> the code constant, EMPTY). A research-risk disclosure is reported
    as what it is and never counts."""
    st = dict(st or {})
    rule = SETTLEMENT_GATE_RULE
    gates = APPROVED_SETTLEMENT_GATES if approved is None else approved
    blockers = list(st.get("blockers") or [])
    lane = list(st.get("lane_refusals") or [])
    facts_ok = (st.get("compatibility") == rule["compatibility_required"]
                and st.get("overall_established") is
                rule["overall_established_required"]
                and len(blockers) <= rule["blockers_allowed"]
                and len(lane) <= rule["lane_refusals_allowed"])
    gate_ok = SETTLEMENT_GATE_ID in (gates or ())
    ok = facts_ok and gate_ok
    return {"status": LIVE_ADMISSIBLE if ok else NOT_ADMISSIBLE,
            "compatibility": st.get("compatibility"),
            "overall_established": st.get("overall_established"),
            "blockers": blockers, "lane_refusals": lane,
            "research_disclosure": st.get("research_disclosure"),
            "research_disclosure_counts": rule["research_disclosure_counts"],
            "gate": SETTLEMENT_GATE_ID, "gate_approved": gate_ok,
            "why": (None if ok else
                    "SETTLEMENT_GATE_APPROVAL_ABSENT_OR_STALE" if facts_ok
                    else "settlement facts are not explicitly compatible")}


def book_currency_admission(bc: dict | None, *, approved=None) -> dict:
    bc = dict(bc or {})
    rules = APPROVED_LIVE_BOOK_RULES if approved is None else approved
    verdict, rule = bc.get("verdict"), bc.get("rule")
    sub = bc.get("subscription_state")
    ok = (verdict == "ESTABLISHED" and rule in rules
          and (sub is None or sub == "RUNNING")
          and not bc.get("gap_since_snapshot"))
    why = None
    if not ok:
        why = ("verdict %r under rule %r%s%s" % (
            verdict, rule,
            "" if rule in rules else " (not an approved live rule)",
            "" if sub in (None, "RUNNING") else "; subscription %r" % sub))
    return {"status": LIVE_ADMISSIBLE if ok else NOT_ADMISSIBLE,
            "verdict": verdict, "rule": rule, "subscription_state": sub,
            "approved_live_rules": sorted(rules), "why": why}


def evaluate(facts: dict | None, *, slug: str | None = None,
             order_intent: str | None = None, live_qty=None,
             approved_book_rules=None,
             approved_settlement_gates=None) -> dict:
    """Pure. {verdict, refusal (first failing), requirements: [...], digest}.
    `slug` / `order_intent` bind the facts to the intent being admitted;
    `live_qty` (when known) is the depth requirement's quantity;
    `approved_settlement_gates` the settlement gate approvals in force
    (None -> APPROVED_SETTLEMENT_GATES, empty)."""
    reqs: list = []

    def put(name, ok, code, **ev):
        reqs.append(dict({"requirement": name, "passed": bool(ok),
                          "refusal": None if ok else code}, **ev))

    if not isinstance(facts, dict) or not facts:
        put("decision_facts_present", False, R_FACTS_ABSENT)
        return {"version": VERSION, "verdict": NOT_ADMISSIBLE,
                "refusal": R_FACTS_ABSENT, "requirements": reqs,
                "runtime_requirements": [n for n, _ in RUNTIME],
                "digest": None}
    # 1 · PinnAPI evidence fresh and exactly mapped
    pr = dict(facts.get("probability") or {})
    p = _num(pr.get("p"))
    age, lim = _num(pr.get("age_s")), _num(pr.get("limit_s"))
    ok1 = (pr.get("authority_basis") == PINNAPI_AUTHORITY
           and pr.get("qualified") is True and p is not None and 0 < p < 1
           and age is not None and lim is not None and 0 <= age <= lim
           and pr.get("mapped") is True)
    put("pinnapi_fresh_and_mapped", ok1, R_PROBABILITY,
        authority_basis=pr.get("authority_basis"), age_s=age, limit_s=lim,
        outcome_books=pr.get("outcome_books"))
    # 2 · exact event / market / outcome / period identity
    idn = dict(facts.get("identity") or {})
    checks = idn.get("checks") or []
    ok2 = (all(_passed(checks, n) for n in REQUIRED_IDENTITY_CHECKS)
           and _passed(checks, "market_and_line")
           and _passed(checks, "grading_period_full_game")
           and bool(idn.get("us_market_slug"))
           and (slug is None or idn.get("us_market_slug") == slug)
           and (order_intent is None or idn.get("order_intent") == order_intent))
    put("exact_identity", ok2, R_IDENTITY,
        us_market_slug=idn.get("us_market_slug"), intent_slug=slug,
        order_intent=idn.get("order_intent"))
    # 3 · executable venue contract
    put("venue_contract", all(_passed(checks, n)
                              for n in REQUIRED_CONTRACT_CHECKS), R_CONTRACT)
    # 4 · book currency under an approved live rule
    book = dict(facts.get("book") or {})
    bca = book_currency_admission(book.get("book_currency"),
                                  approved=approved_book_rules)
    put("book_currency_live", bca["status"] == LIVE_ADMISSIBLE,
        R_BOOK_CURRENCY, **{k: bca[k] for k in
                            ("verdict", "rule", "subscription_state", "why")})
    # 5 · market tradable
    ms = book.get("market_state")
    put("market_tradable", str(ms) in TRADABLE_MARKET_STATES, R_TRADABLE,
        market_state=ms)
    # 6 · executable price known
    px = dict(facts.get("price") or {})
    wire = _num(px.get("wire"))
    put("executable_price_known", wire is not None and 0 < wire < 1, R_PRICE,
        wire=wire)
    # 7 · required depth available
    depth = _num(book.get("depth_within_limit"))
    need = _num(live_qty) if live_qty is not None else 1.0
    put("depth_available", depth is not None and need is not None
        and depth >= need > 0, R_DEPTH, depth_within_limit=depth,
        required_qty=need)
    # 8 · fees known
    fe = dict(facts.get("fees") or {})
    put("fees_known", fe.get("known") is True and _num(fe.get("fees_usd"))
        is not None, R_FEES, fees_usd=fe.get("fees_usd"), model=fe.get("model"))
    # 9 · slippage represented
    sl = dict(facts.get("slippage") or {})
    put("slippage_represented", _pos(sl.get("model"))
        and _num(sl.get("max_price")) is not None
        and (wire is None or _num(sl.get("max_price")) <= wire + 1e-12),
        R_SLIPPAGE, model=sl.get("model"), max_price=sl.get("max_price"))
    # 10 · net EV still qualifies
    ev = dict(facts.get("economics") or {})
    nev = _num(ev.get("net_expected_profit_usd"))
    put("net_ev_positive", ev.get("net_ev_positive") is True
        and nev is not None and nev > 0, R_NET_EV,
        net_expected_profit_usd=nev)
    # 11 · settlement explicitly LIVE_ADMISSIBLE
    sa = settlement_admission(facts.get("settlement"),
                              approved=approved_settlement_gates)
    put("settlement_live_admissible", sa["status"] == LIVE_ADMISSIBLE,
        R_SETTLEMENT, compatibility=sa["compatibility"],
        overall_established=sa["overall_established"],
        blockers=sa["blockers"],
        research_disclosure=sa["research_disclosure"],
        gate_approved=sa["gate_approved"], why=sa["why"])
    failed = [r for r in reqs if not r["passed"]]
    return {"version": VERSION,
            "verdict": NOT_ADMISSIBLE if failed else LIVE_ADMISSIBLE,
            "refusal": failed[0]["refusal"] if failed else None,
            "refusals": [r["refusal"] for r in failed],
            "requirements": reqs,
            "runtime_requirements": [n for n, _ in RUNTIME],
            "book_currency": bca, "settlement": sa,
            "digest": facts_digest(facts)}
