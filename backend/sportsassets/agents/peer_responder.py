"""THE PEER RESPONSE TO KAREN: DEREK, XAVIER, AUDREY (AND THE CHIEF
ALLOCATOR) ANSWER HER CHALLENGES; AN INDEPENDENT EVALUATOR DECIDES.

Rule-based, grounded and failure-isolated. This is NOT Karen's code: Karen's
modules never call it, and it never writes as Karen.

  1. RESPONSE (as the TARGET). For each OPEN challenge against an agent,
     that agent's responder re-applies THE SAME RULE the challenge's detector
     applied (karen_runner.rule_holds -- literally the same SQL predicate) to
     the challenged record:
       * the rule still holds  -> CONCEDE, citing the record ("the record
         really is defective under the same rule");
       * the rule no longer holds -> DISPUTE, citing the record as it now
         stands (and any record that cured it);
       * the rule cannot be re-applied (a manual challenge, an absent table,
         a vanished record) -> no automatic response; a person answers.
     The response is recorded through karen.respond, so only the target can
     answer and every cited record must exist.
  2. INDEPENDENT EVALUATION. For each RESPONDED challenge, the evaluator --
     Audrey for Derek, Xavier and the Chief Allocator; Xavier for Audrey;
     never Karen and never the target -- re-applies the rule itself and
     records the outcome through karen.resolve with its evidence:
       * CONCEDE -> UPHELD (the conceded defect, re-checked);
       * DISPUTE and the rule holds -> UPHELD (the dispute does not stand);
       * DISPUTE and the rule no longer holds -> REJECTED.
     The database refuses Karen resolving, the target rejecting, and
     anyone but a third party resolving a dispute (migrations 207 / 212).

  3. THE DURABLE QUEUE (agents/agent_work.py, migration 234): every OPEN
     challenge is a CHALLENGE_RESPONSE item of its target, every RESPONDED
     one a CHALLENGE_EVALUATION item of its evaluator (depending on the
     response item); a challenge the rule cannot be re-applied to is a
     BLOCKED attempt (a person answers), a refusal a BLOCKED attempt naming
     it; the recorded response / outcome completes the item.

It writes only karen_challenges / karen_challenge_events (through karen.py),
its agent_work_* queue records and, for an agent that answered, an
agent_runs row. It holds no order,
approval, activation, limit or control path.

Off switch: PEER_RESPONDER_ENABLED=0 (default on).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from . import karen as K
from . import karen_runner as KR
from . import registry as R

log = logging.getLogger(__name__)

INTERVAL_S = 120
FIRST_DELAY_S = 90
PASS_TIMEOUT_S = 60
MAX_PER_AGENT = 10
SERVICE = "agent_peer_responder"
RESPONDERS = K.TARGETS
EVALUATORS = tuple(sorted(set(K.EVALUATOR_FOR.values())))


def enabled() -> bool:
    return os.getenv("PEER_RESPONDER_ENABLED", "1").strip().lower() not in (
        "0", "false", "off", "no")


def evaluator_for(target: str) -> str:
    ev = K.EVALUATOR_FOR[target]
    assert ev not in (target, K.KAREN)
    return ev


def _ref(c) -> dict:
    return {"kind": c["target_kind"], "id": str(c["target_id"])}


async def _recheck(conn, c) -> bool | None:
    try:
        return await KR.rule_holds(conn, c["detector"], c["target_kind"],
                                   c["target_id"])
    except Exception:                                           # noqa: BLE001
        return None


async def respond_as(conn, agent: str, *, now: float,
                     limit: int = MAX_PER_AGENT) -> dict:
    """THE TARGET'S ANSWERS: CONCEDE where the same rule still holds, else
    DISPUTE with evidence. Never raises."""
    out = {"agent": agent, "conceded": [], "disputed": [], "skipped": [],
           "refused": {}}
    if agent not in RESPONDERS or agent == K.KAREN:
        out["refused"]["*"] = K.R_NOT_THE_TARGET
        return out
    rows = await conn.fetch(
        "SELECT challenge_id, detector, target_kind, target_id, "
        "       challenged_at FROM karen_challenges "
        " WHERE target_agent=$1 AND state='OPEN' "
        "   AND challenged_at <= to_timestamp($2) "
        " ORDER BY challenged_at, challenge_id LIMIT $3",
        agent, float(now), int(limit))
    for c in rows:
        holds = await _recheck(conn, c)
        if holds is None:
            out["skipped"].append(c["challenge_id"])
            continue
        at = max(float(now), c["challenged_at"].timestamp())
        if holds:
            text = ("Conceded: re-applying the same rule (%s) to %s %s, it "
                    "still holds. The record is defective as challenged."
                    % (c["detector"], c["target_kind"], c["target_id"]))
            stance = "CONCEDE"
        else:
            text = ("Disputed: re-applying the same rule (%s) to %s %s, it "
                    "does not hold for the record as it now stands; see the "
                    "cited record." % (c["detector"], c["target_kind"],
                                       c["target_id"]))
            stance = "DISPUTE"
        got = await K.respond(conn, c["challenge_id"], agent=agent,
                              stance=stance, response=text, at=at,
                              evidence_refs=[_ref(c)])
        if got.get("ok"):
            out["conceded" if holds else "disputed"].append(c["challenge_id"])
        else:
            out["refused"][c["challenge_id"]] = got.get("refusal")
    return out


async def evaluate_as(conn, evaluator: str, *, now: float,
                      limit: int = MAX_PER_AGENT) -> dict:
    """THE INDEPENDENT EVALUATION of every RESPONDED challenge whose target
    `evaluator` evaluates. Never raises."""
    out = {"evaluator": evaluator, "upheld": [], "rejected": [],
           "skipped": [], "refused": {}}
    targets = [t for t, e in K.EVALUATOR_FOR.items() if e == evaluator]
    if not targets or evaluator == K.KAREN:
        return out
    rows = await conn.fetch(
        "SELECT challenge_id, detector, target_agent, target_kind, "
        "       target_id, response_stance, response_evidence_refs, "
        "       responded_at FROM karen_challenges "
        " WHERE state='RESPONDED' AND target_agent = ANY($1::text[]) "
        "   AND responded_at <= to_timestamp($2) "
        " ORDER BY responded_at, challenge_id LIMIT $3",
        targets, float(now), int(limit))
    for c in rows:
        if evaluator in (c["target_agent"], K.KAREN):
            out["refused"][c["challenge_id"]] = K.R_NOT_INDEPENDENT
            continue
        holds = await _recheck(conn, c)
        at = max(float(now), c["responded_at"].timestamp())
        if c["response_stance"] == "CONCEDE":
            outcome = K.UPHELD
            reason = ("Independent evaluation by %s: the target conceded; the "
                      "rule %s %s on re-check." % (
                          evaluator.title(), c["detector"],
                          "still holds" if holds else
                          "held at the challenge (record since changed)"
                          if holds is False else "was not re-checkable"))
        elif holds is None:
            out["skipped"].append(c["challenge_id"])
            continue
        elif holds:
            outcome = K.UPHELD
            reason = ("Independent evaluation by %s: the dispute does not "
                      "stand; the rule %s still holds for %s %s."
                      % (evaluator.title(), c["detector"], c["target_kind"],
                         c["target_id"]))
        else:
            outcome = K.REJECTED
            reason = ("Independent evaluation by %s: the rule %s no longer "
                      "holds for %s %s; the challenge is rejected."
                      % (evaluator.title(), c["detector"], c["target_kind"],
                         c["target_id"]))
        got = await K.resolve(conn, c["challenge_id"], resolver=evaluator,
                              outcome=outcome, reason=reason, at=at,
                              evidence_refs=[_ref(c)])
        if got.get("ok"):
            out["upheld" if outcome == K.UPHELD else "rejected"].append(
                c["challenge_id"])
        else:
            out["refused"][c["challenge_id"]] = got.get("refusal")
    return out


async def pass_once(conn, *, now: float | None = None) -> dict:
    """Every responder answers, then every evaluator evaluates. An agent
    that recorded something gets an agent_runs row. Never raises."""
    at = float(now if now is not None else time.time())
    summary: dict = {"responses": {}, "evaluations": {}, "errors": {},
                     "authority": "NONE"}
    if not await K.schema(conn):
        summary["status"] = K.R_NO_SCHEMA
        return summary
    for agent in RESPONDERS:
        try:
            summary["responses"][agent] = await respond_as(conn, agent,
                                                           now=at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            summary["errors"]["respond:" + agent] = type(exc).__name__
    for ev in EVALUATORS:
        try:
            summary["evaluations"][ev] = await evaluate_as(conn, ev, now=at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            summary["errors"]["evaluate:" + ev] = type(exc).__name__
    # THE DURABLE QUEUE (migration 234): what each side could not finish
    from . import agent_work as AW
    blocked_r: dict = {}
    for r in summary["responses"].values():
        for cid in r.get("skipped") or []:
            blocked_r[cid] = {"outcome": AW.O_BLOCKED,
                              "blocker": "RULE_NOT_REAPPLICABLE_A_PERSON_"
                                         "ANSWERS", "next_in_s": INTERVAL_S}
        for cid, why in (r.get("refused") or {}).items():
            if cid != "*":
                blocked_r[cid] = {"outcome": AW.O_BLOCKED,
                                  "blocker": str(why or "REFUSED"),
                                  "next_in_s": INTERVAL_S}
    blocked_e: dict = {}
    for r in summary["evaluations"].values():
        for cid in r.get("skipped") or []:
            blocked_e[cid] = {"outcome": AW.O_BLOCKED,
                              "blocker": "RULE_NOT_REAPPLICABLE_A_PERSON_"
                                         "EVALUATES", "next_in_s": INTERVAL_S}
        for cid, why in (r.get("refused") or {}).items():
            blocked_e[cid] = {"outcome": AW.O_BLOCKED,
                              "blocker": str(why or "REFUSED"),
                              "next_in_s": INTERVAL_S}
    summary["work_queue"] = await AW.sync_for(
        conn, "peer_responder", now=at,
        attempts={AW.K_RESPONSE: blocked_r, AW.K_EVALUATION: blocked_e})
    acted: dict[str, dict] = {}
    for a, r in summary["responses"].items():
        if r["conceded"] or r["disputed"]:
            acted.setdefault(a, {})["responded"] = (r["conceded"]
                                                    + r["disputed"])
    for a, r in summary["evaluations"].items():
        if r["upheld"] or r["rejected"]:
            acted.setdefault(a, {})["evaluated"] = r["upheld"] + r["rejected"]
    for a, what in acted.items():
        if a not in R.IDENTITIES:
            continue                       # the Chief Allocator is a role
        rid = "peer-response:%s:%s" % (a.lower(), uuid.uuid4().hex)
        await R.start_run(conn, a, rid, now=at,
                          summary={"kind": "KAREN_PEER_RESPONSE"})
        await R.finish_run(conn, a, rid, outcome="PEER_RESPONSE_RECORDED",
                           summary=what, now=at)
    summary["status"] = "ERRORS" if summary["errors"] else "OK"
    try:
        from .. import db as _db
        await _db.heartbeat(SERVICE, "ok" if not summary["errors"]
                            else "error", {"status": summary["status"],
                                           "acted": acted,
                                           "errors": summary["errors"]},
                            con=conn)
    except Exception:                                           # noqa: BLE001
        pass
    return summary


async def run(get_pool, *, interval_s: float = INTERVAL_S,
              first_delay_s: float = FIRST_DELAY_S) -> None:
    """The loop, armed from the API lifespan. Bounded per pass, isolated."""
    if not enabled():
        log.info("peer responder disabled (PEER_RESPONDER_ENABLED)")
        return
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            pool = await get_pool()
            async with asyncio.timeout(PASS_TIMEOUT_S):
                async with pool.acquire() as conn:
                    await pass_once(conn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                # noqa: BLE001
            log.warning("peer responder: pass failed (%s)",
                        type(exc).__name__)
        await asyncio.sleep(interval_s)
