"""SHARED FIXTURES FOR THE AGENT-CORE PROOFS (1002). Not a test module.

* `clean_agents(conn)` -- removes every agent-core row (migration 152) in a
  TEST database; the two append-only tables are cleared with triggers
  suspended for that one transaction only.
* `install(monkeypatch, name, **funcs)` -- installs a STAND-IN for another
  stream's module (`sportsassets.agents.<name>`) so a proof can make a hook
  answer, raise or hang; `uninstall(monkeypatch, name)` makes the module
  unimportable (a stream that is not deployed).
* `status(conn, agent)` -- the agent's status row as a dict.
* `no_order_calls(sent)` -- the venue calls that would move an order.
"""
from __future__ import annotations

import sys
import types

AGENT_TABLES = ("agent_handoff_fills", "agent_position_handoffs",
                "agent_decisions", "agent_task_events", "agent_tasks",
                "agent_policy_versions", "agent_runs", "agent_status",
                "agent_identities")

ORDER_CALLS = ("create", "preview", "orders.cancel")


async def clean_agents(conn) -> None:
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        for t in AGENT_TABLES:
            await conn.execute("DELETE FROM %s" % t)


def install(monkeypatch, name: str, **funcs) -> types.ModuleType:
    full = "sportsassets.agents.%s" % name
    mod = types.ModuleType(full)
    for k, v in funcs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, full, mod)
    import sportsassets.agents as pkg
    monkeypatch.setattr(pkg, name, mod, raising=False)
    return mod


def uninstall(monkeypatch, name: str) -> None:
    """`import sportsassets.agents.<name>` now raises ModuleNotFoundError."""
    monkeypatch.setitem(sys.modules, "sportsassets.agents.%s" % name, None)


async def status(conn, agent: str) -> dict:
    r = await conn.fetchrow("SELECT * FROM agent_status WHERE agent_id=$1",
                            agent)
    return dict(r) if r else {}


def order_calls(sent) -> list:
    return [(k, p) for k, p in sent if k in ORDER_CALLS]


def enter_gate(monkeypatch, verdict: str = "ENTER", calls=None):
    """A stand-in Derek policy module answering `verdict`."""
    async def gate_for_funded_entry(conn, rec, *, now):
        if calls is not None:
            calls.append({"rec": rec, "now": now})
        return {"verdict": verdict, "refusal": None if verdict == "ENTER"
                else "STAND_IN_POLICY_SAYS_%s" % verdict}
    return install(monkeypatch, "derek_policy",
                   gate_for_funded_entry=gate_for_funded_entry)
