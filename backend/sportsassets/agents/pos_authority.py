"""ARCHER, SCOUT AND ADRIANA HOLD NO AUTHORITY: ONE SHARED REFUSAL, IN CODE.

Archer (Head of Execution, SHADOW_ONLY; named EDDIE until migration 266),
Scout (Market Intelligence, RESEARCH_SHADOW_ONLY) and Adriana (Head of
Arbitrage, SHADOW_ONLY, migration 265) are persisted agents (migration 217)
with NO venue submission, order,
cancel, credential, capital, approval, activation, policy or promotion
authority. This module is the code half of that rule:

  * `may(agent, tool)` -- on the agent's registry allow list, not on its
    deny list, and not a forbidden action or prefix (order.*, dispatch.*,
    request.*, cancel.*, promote.*);
  * `refuse_authority(agent, action)` -- the one named answer to any such
    request;
  * `is_actor(agent, v)` -- mirrors the database's pos_agent_actor();
  * `act_as(conn, agent)` -- declares the transaction's acting agent
    (`bettor.acting_agent`), so the database's no-authority trigger refuses
    any order / intent / fill / approval / control write made in it.

The database half (migration 217) refuses the same things on its own.

THE HISTORICAL ALIAS (266). "EDDIE" -- the id, or a machine-style label such
as agent:eddie or eddie-bot -- is still recognised as an ACTING identity and
resolves to ARCHER, so it is refused exactly as Archer is (the database's
pos_agent_actor() does the same). It is never an agent of its own: `may`
grants it nothing and `act_as` refuses it (new writes use ARCHER).
"""
from __future__ import annotations

import re

from . import registry as R

ARCHER, SCOUT, ADRIANA = R.ARCHER, R.SCOUT, R.ADRIANA
AGENTS = R.SHADOW_AGENTS
AUTHORITY_STATUS = {ARCHER: "SHADOW_ONLY", SCOUT: "RESEARCH_SHADOW_ONLY",
                    ADRIANA: "SHADOW_ONLY"}

#: What neither may ever do -- each also on their registry DENY lists.
FORBIDDEN_ACTIONS = (
    "order.submit_direct", "order.cancel_direct", "request.funded_entry",
    "dispatch.xavier_claim", "write.risk_limits", "write.credentials",
    "write.account_authority", "write.approvals",
    "write.submission_switches", "deploy", "write.policy_candidates",
    "write.policy_activation", "promotion", "write.entry_decisions",
    "write.management_decisions", "write.directives",
    "write.feature_promotion", "write.capital_allocation",
    "write.release_eligibility", "write.challenges")
FORBIDDEN_PREFIXES = ("order.", "dispatch.", "request.", "cancel.",
                      "promote.", "submit.")

R_NO_AUTHORITY = {ARCHER: "ARCHER_HAS_NO_AUTHORITY",
                  SCOUT: "SCOUT_HAS_NO_AUTHORITY",
                  ADRIANA: "ADRIANA_HAS_NO_AUTHORITY"}
WHY = {
    ARCHER: ("Archer estimates execution in SHADOW and recommends; he holds no "
            "venue submission, order, cancel, capital, approval, activation "
            "or promotion authority"),
    SCOUT: ("Scout researches and tests features in SHADOW; he holds no "
            "trade, portfolio, policy-approval or feature-promotion "
            "authority and cannot validate his own feature"),
    ADRIANA: ("Adriana proves or refuses arbitrage structures in SHADOW; she "
              "holds no venue submission, order, cancel, credential, "
              "capital, approval, activation or promotion authority"),
}

_RX = {
    ARCHER: (re.compile(r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+ARCHER([^A-Z]|$)"),
            re.compile(r"ARCHER[ ._:-]*(AGENT|BOT|EXECUTION)")),
    SCOUT: (re.compile(r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+SCOUT([^A-Z]|$)"),
            re.compile(r"SCOUT[ ._:-]*(AGENT|BOT|INTEL|RESEARCH)")),
    ADRIANA: (re.compile(r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+ADRIANA([^A-Z]|$)"),
              re.compile(r"ADRIANA[ ._:-]*(AGENT|BOT|ARB|ARBITRAGE)")),
}
#: (266) the historical alias's acting-identity labels -> the agent it names
#: now. Exactly migration 217's EDDIE patterns, so every label the database
#: refused as EDDIE is still refused -- as ARCHER.
_ALIAS_RX = {
    R.EDDIE_ALIAS: (
        R.HISTORICAL_ALIASES[R.EDDIE_ALIAS],
        (re.compile(r"^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+EDDIE([^A-Z]|$)"),
         re.compile(r"EDDIE[ ._:-]*(AGENT|BOT|EXECUTION)"))),
}


class NoAuthority(PermissionError):
    """Raised by `assert_may` for anything Archer or Scout may not do."""


def actor_of(v) -> str | None:
    """ARCHER / SCOUT / ADRIANA when `v` names one as an acting identity (the
    id or a machine-style label), else None. A person named Archer (or
    Eddie) is not. The historical alias EDDIE resolves to ARCHER."""
    if not isinstance(v, str):
        return None
    a = v.strip().upper()
    for agent in AGENTS:
        if a == agent or any(rx.search(a) for rx in _RX[agent]):
            return agent
    for alias, (agent, rxs) in _ALIAS_RX.items():
        if a == alias or any(rx.search(a) for rx in rxs):
            return agent
    return None


def is_actor(agent: str, v) -> bool:
    return actor_of(v) == str(agent or "").upper()


def may(agent: str, tool: str) -> bool:
    a = str(agent or "").upper()
    t = str(tool or "")
    if a not in AGENTS:
        return False
    if t in FORBIDDEN_ACTIONS or t.startswith(FORBIDDEN_PREFIXES):
        return False
    return R.permits(a, t)


def assert_may(agent: str, tool: str) -> None:
    if not may(agent, tool):
        a = str(agent or "").upper()
        raise NoAuthority("%s: %s" % (R_NO_AUTHORITY.get(a, "NO_AUTHORITY"),
                                      tool))


def refuse_authority(agent: str, action: str) -> dict:
    a = R.canonical_agent_id(agent) or ""
    out = {"ok": False,
           "refusal": R_NO_AUTHORITY.get(a, "NO_AUTHORITY"),
           "action": str(action), "authority": AUTHORITY_STATUS.get(a),
           "why": WHY.get(a, "no authority")}
    if R.historical_alias(agent):
        out["historical_alias"] = R.historical_alias(agent)
    return out


async def act_as(conn, agent: str) -> None:
    """Declare this TRANSACTION's acting agent to the database. Inside a
    transaction it lasts until commit / rollback; the no-authority trigger
    then refuses any order, intent, fill, approval or control write."""
    a = str(agent or "").upper()
    if a not in AGENTS:
        raise ValueError("UNKNOWN_SHADOW_AGENT")
    await conn.execute("SELECT set_config('bettor.acting_agent', $1, true)",
                       a)


def profile(agent: str) -> dict:
    a = str(agent or "").upper()
    ident = R.IDENTITIES[a]
    return {"agent_id": a, "display_name": ident["display_name"],
            "role": ident["role"], "mandate": ident["mandate"],
            "authority": AUTHORITY_STATUS[a],
            "tool_permissions": ident["tool_permissions"],
            "forbidden_actions": list(FORBIDDEN_ACTIONS),
            "enforced_by": ["agents/registry.permits",
                            "agents/pos_authority.may / refuse_authority",
                            "migration 217 CHECKs and the "
                            "aa_pos_agents_no_authority_trg trigger on every "
                            "approval, control, order, intent and fill "
                            "table"]}
