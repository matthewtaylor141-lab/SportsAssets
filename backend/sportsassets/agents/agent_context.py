"""EACH AGENT'S REASONING CONTRACT: a ROLE-SPECIFIC tool and context bundle.

Section 11 of the HQ2 directive: Derek must not receive Audrey's bundle,
Karen receives no authority tool, Scout sees no live-order mutation tool,
Eddie holds no capital-approval authority, and Xavier's management context
carries the current valuation / review identity.

A BUNDLE (pure, `bundle(agent)`) is
  tools            the agent's ALLOWED tools -- exactly the registry's allow
                   list (identity.tool_permissions), never widened here
  denied           its deny list (every NEVER_GRANTED tool included)
  context_sources  the record kinds its context is read from
  focus            its research focus (role_brief.FOCUS; the Chief
                   Allocator's is defined here, it has no role brief)
  principles       its identity's decision principles
  memory_scope     OWN_PRIVATE_ONLY: its own memory, hand-offs addressed to
                   it and shared objective facts -- never another agent's
                   private memory
  requires         extra context the role must carry (Xavier: the current
                   review identity, filled-only protection)

`build_context(conn, agent)` assembles the bundle with its records: the
agent's own memories (agent_memory.private_memories with reader = the agent
itself), the hand-offs addressed to it and, for Xavier, ONE CURRENT review
per position from xavier_freshness.current_decisions (review id, valuation
id / version, management state; a stale newest review is
WAITING_FOR_FRESH_EVIDENCE, never HOLD) and the order_state_truth rule that
only FILLED quantity is protection. Reads only.
"""
from __future__ import annotations

import time

from .. import order_state_truth as OST
from .. import xavier_freshness as XF
from . import agent_memory as M
from . import identity as I
from . import registry as R
from . import role_brief as RB

VERSION = "AGENT_CONTEXT_V1"

#: Tools that carry ANY authority over orders, capital, policy, approvals or
#: production. Karen's bundle may hold none of them.
AUTHORITY_TOOLS = frozenset({
    "request.funded_entry", "dispatch.xavier_claim", "order.submit_direct",
    "order.cancel_direct", "deploy", "write.risk_limits",
    "write.credentials", "write.account_authority", "write.approvals",
    "write.submission_switches", "write.policy_activation", "promotion",
    "write.feature_promotion", "write.capital_allocation",
    "write.release_eligibility", "write.policy_candidates",
    "write.directives"})
#: Tools that can create, change or cancel a live order.
ORDER_MUTATION_TOOLS = frozenset({"request.funded_entry",
                                  "dispatch.xavier_claim",
                                  "order.submit_direct",
                                  "order.cancel_direct"})
#: Capital / approval authority.
CAPITAL_APPROVAL_TOOLS = frozenset({"write.capital_allocation",
                                    "write.approvals", "write.risk_limits",
                                    "write.policy_activation", "promotion"})

ALLOCATOR_FOCUS = (
    "Capital allocation, SHADOW only. Rank qualified candidates and open "
    "positions on correlation (established event, settlement and side "
    "identity -- never similar titles), capacity, concentration, "
    "capital-hours, opportunity cost and marginal portfolio contribution. "
    "One attractive position never outranks portfolio integrity. Your "
    "weights size no order and commit no capital.")

CONTEXT_SOURCES = {
    R.DEREK: ("paper_decisions", "external_valuations", "us_premap",
              "paper_book_observations", "eddie_execution_estimates"),
    R.XAVIER: ("paper_xavier_reviews", "xavier_management_assessments",
               "paper_orders", "paper_fills", "paper_handoffs",
               "paper_settlements"),
    R.AUDREY: ("paper_audrey_findings", "paper_audrey_reports",
               "audrey_audit_reports", "karen_challenges",
               "smalllive_reconciliations"),
    R.KAREN: ("karen_challenges", "agent_decisions", "paper_decisions",
              "paper_xavier_reviews", "execution_intents",
              "intel_allocations"),
    I.CHIEF_ALLOCATOR: ("intel_runs", "intel_allocations",
                        "paper_decisions", "paper_handoffs"),
    R.EDDIE: ("eddie_execution_estimates", "eddie_execution_outcomes",
              "paper_book_observations", "paper_decisions"),
    R.SCOUT: ("scout_sources", "scout_features",
              "scout_feature_observations", "scout_feature_tournaments",
              "external_valuations"),
}

REQUIRES = {
    R.XAVIER: ("current_review_identity", "filled_only_protection"),
}


def bundle(agent: str) -> dict:
    """THE AGENT'S TOOL / CONTEXT BUNDLE. Pure; derived from the registry,
    the identity and the role brief -- nothing here grants a tool."""
    a = I.agent_of(agent)
    if a is None:
        raise ValueError(I.R_UNKNOWN_AGENT)
    perms = I.tool_permissions(a)
    ident = I.IDENTITY_SPEC[a]
    allowed = sorted(t for t in perms["allowed"] if I.permits(a, t))
    return {"version": VERSION, "agent": a,
            "authority_status": ident["authority_status"],
            "tools": allowed,
            "denied": sorted(set(perms["denied"]) | set(R.NEVER_GRANTED)),
            "order_path": perms.get("order_path"),
            "context_sources": list(CONTEXT_SOURCES[a]),
            "focus": RB.FOCUS.get(a) or ALLOCATOR_FOCUS,
            "principles": list(ident["decision_principles"]),
            "memory_scope": "OWN_PRIVATE_ONLY+HANDOFFS_TO_ME+SHARED_FACTS",
            "requires": list(REQUIRES.get(a, ())),
            "rules": (["exactly one CURRENT review per position; older "
                       "reviews are SUPERSEDED (superseded_by); a stale "
                       "newest review is WAITING_FOR_FRESH_EVIDENCE, never "
                       "HOLD", OST.RULE] if a == R.XAVIER else [])}


def fingerprint(b: dict) -> tuple:
    """What makes two bundles different (for the tests and the API)."""
    return (tuple(b["tools"]), tuple(b["context_sources"]), b["focus"])


XAVIER_REVIEWS_SQL = """
SELECT review_id, group_id, reviewed_at, recommendation, refusal, measure,
       selection
  FROM paper_xavier_reviews
 WHERE group_id IN (SELECT group_id FROM paper_xavier_reviews
                     ORDER BY reviewed_at DESC LIMIT $1)
 ORDER BY reviewed_at DESC, review_id DESC LIMIT $2"""


async def xavier_current_reviews(conn, *, now: float, groups: int = 10
                                 ) -> list:
    """ONE CURRENT DECISION PER RECENT POSITION (xavier_freshness): the
    review id, valuation id / version, management state and the ids of the
    reviews it superseded. Raises on a failed read (the caller reports it)."""
    rows = [dict(r) for r in await conn.fetch(XAVIER_REVIEWS_SQL,
                                              groups * 5, groups * 20)]
    for r in rows:
        if hasattr(r.get("reviewed_at"), "timestamp"):
            r["reviewed_at"] = r["reviewed_at"].timestamp()
    out = []
    for g, d in XF.current_decisions(rows, now=now,
                                     limit_s=XF.default_limit_s()).items():
        dec = d["current"]["decision"]
        out.append({"group_id": g, "review_id": dec["review_id"],
                    "valuation_id": dec["valuation_id"],
                    "valuation_version": dec["valuation_version"],
                    "recommendation_state": dec["recommendation_state"],
                    "management_state": dec["management_state"],
                    "current_recommendation": dec["current_recommendation"],
                    "recorded_recommendation": dec["recorded_recommendation"],
                    "superseded": [{"review_id": o["review_id"],
                                    "superseded_by": o["superseded_by"]}
                                   for o in d["superseded"][:5]],
                    "evidence": {"kind": "paper_xavier_reviews",
                                 "id": dec["review_id"]}})
    return sorted(out, key=lambda x: str(x["group_id"]))


async def build_context(conn, agent: str, *, now: float | None = None,
                        memory_limit: int = 20) -> dict:
    """THE AGENT'S CONTEXT: its bundle, its OWN memories, the hand-offs
    addressed to it and (Xavier) the current review identity. Each section
    is OK / ABSENT / UNAVAILABLE with its reason. Reads only."""
    a = I.agent_of(agent)
    at = float(now if now is not None else time.time())
    out = {"bundle": bundle(a), "computed_at": at, "sections": {}}

    async def sect(name, coro):
        try:
            async with conn.transaction():
                v = await coro
            out["sections"][name] = {"status": "OK" if v else "EMPTY"}
            return v
        except Exception as exc:                                # noqa: BLE001
            out["sections"][name] = {"status": "UNAVAILABLE",
                                     "why": type(exc).__name__}
            return None

    if await M.has_schema(conn):
        out["own_memories"] = await sect("own_memories", M.private_memories(
            conn, reader=a, owner=a, limit=memory_limit,
            include_superseded=False))
        out["handoffs_to_me"] = await sect("handoffs_to_me",
                                           M.handoffs_to(conn, a))
    else:
        out["sections"]["own_memories"] = {"status": "ABSENT",
                                           "why": M.R_NO_SCHEMA}
        out["own_memories"] = out["handoffs_to_me"] = None
    if a == R.XAVIER:
        if await conn.fetchval(
                "SELECT to_regclass('paper_xavier_reviews') IS NOT NULL"):
            out["current_reviews"] = await sect(
                "current_reviews", xavier_current_reviews(conn, now=at))
        else:
            out["current_reviews"] = None
            out["sections"]["current_reviews"] = {
                "status": "ABSENT", "why": "TABLE_NOT_DEPLOYED"}
        out["protection_rule"] = OST.RULE
    return out
