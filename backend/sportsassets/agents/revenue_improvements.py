"""AUDREY'S REVENUE-RELIABILITY PROPOSALS (hook: revenue_improvements.propose_due).

Runs in Audrey's slow half. It reads the economic readback
(sportsassets.revenue_reliability, READ ONLY) and, for each VERIFIED
weakness, records ONE governed improvement candidate through the existing
migration-155 framework (improvement.propose) with its own holdout:

  * proposer  AUDREY (an operating agent; shadow agents cannot propose);
  * evaluator a separate evaluator, recorded later through the framework;
  * approver  a named person -- every class here is REQUIRES_APPROVAL, never
              pre-authorized, with no in-process evaluator, so nothing here
              can release, activate, size, order or change authority;
  * criteria  the CLASS's success / harm metrics, fixed in code before any
              evaluation (no retrospective tuning).

Idempotent: a candidate id is deterministic per methodology version and
class, and an existing candidate is never proposed again. Throttled to one
attempt per THROTTLE_S per process. Never raises (the hook wrapper bounds it).
"""
from __future__ import annotations

import time

from . import improvement as IMP

VERSION = "REVENUE_RELIABILITY_PROPOSALS_V1"
PROPOSER = "AUDREY"
THROTTLE_S = 6 * 3600.0
EVAL_WINDOW_S = 14 * 86400.0
HOLDOUT_BUDGET = 3
_LAST: dict = {}

#: verified weakness kind -> (change class, the behaviour it may change)
CLASSES = {
    ("DEREK", "SEGMENT_CALIBRATION"): ("REVENUE_DEREK_SEGMENT_CALIBRATION",
                                       "Derek's entry probability per sport x family x regime"),
    ("XAVIER", "MANAGEMENT_POLICY"): ("REVENUE_XAVIER_MANAGEMENT_POLICY",
                                      "Xavier's management policy for open PAPER positions"),
    ("KAREN", "CHALLENGE_DETECTOR"): ("REVENUE_KAREN_CHALLENGE_DETECTOR",
                                      "what Karen records about a challenge's downstream effect"),
}


def task_id_for(change_class: str) -> str:
    return "rr:v1:%s" % change_class


def _hypothesis(agent: str, a: dict) -> str:
    lb = a.get("lower_bound_incremental_value_per_event_usd")
    if agent == "KAREN":
        return ("Karen's %s challenges blocked nothing and recorded no downstream impact, so her economic "
                "value is unmeasurable; recording the downstream effect of each challenge would make saved "
                "loss and false-block cost assessable" % a.get("challenges"))
    return ("%s's measured incremental value is not positive (realized %.2f USD over %d independent events, "
            "event-clustered lower bound %s USD/event); a change confined to %s can be shown, on a held-out "
            "set of events, to raise that lower bound above zero"
            % (a.get("display_name"), float(a.get("realized_contribution_usd") or 0.0),
               int(a.get("independent_events") or 0), "unmeasured" if lb is None else "%.2f" % lb,
               CLASSES[(agent, "SEGMENT_CALIBRATION" if agent == "DEREK" else "MANAGEMENT_POLICY")][1]))


async def propose_due(conn, *, now: float | None = None, force: bool = False) -> dict:
    at = float(now if now is not None else time.time())
    if not force and at - _LAST.get("at", 0.0) < THROTTLE_S:
        return {"version": VERSION, "skipped": "THROTTLED", "last_at": _LAST.get("at")}
    _LAST["at"] = at
    if not await IMP.has_schema(conn):
        return {"version": VERSION, "ok": False, "refusal": "IMPROVEMENT_SCHEMA_ABSENT"}
    from .. import bettor_paper_ledger as L
    from ..revenue_reliability import read as RR
    got = await RR.read(conn, account_id=L.ACCOUNT_ID, now=at)
    if got.get("status") != "OK":
        return {"version": VERSION, "ok": False, "refusal": "READBACK_UNAVAILABLE", "why": got.get("why")}
    data = got["data"]
    board = data["agent_scoreboard"]
    out = {"version": VERSION, "ok": True, "proposed": [], "existing": [], "refused": []}
    for w in data.get("verified_weaknesses") or []:
        key = (w["agent"], w["kind"])
        if key not in CLASSES:
            continue
        cls, behaviour = CLASSES[key]
        task = task_id_for(cls)
        cid = IMP.candidate_id_for(task, cls, "v1")
        if await IMP.candidate(conn, cid):
            out["existing"].append(cid)
            continue
        a = board[w["agent"]]
        holdout = "rr:v1:holdout:%s" % w["agent"].lower()
        async with conn.transaction():
            await IMP.ensure_holdout(
                conn, holdout_id=holdout,
                description="Revenue Reliability V1 held-out canonical events for %s" % a["display_name"],
                rule={"unit": "CANONICAL_EVENT", "method": "sha256(event_id + salt) mod 100 < 30",
                      "salt": holdout, "percent": 30, "never_used_for_training": True},
                budget=HOLDOUT_BUDGET)
            res = await IMP.propose(
                conn, task_id=task, change_class=cls, proposed_by=PROPOSER, variant="v1",
                hypothesis=_hypothesis(w["agent"], a),
                evidence={"source": "/api/command/revenue-readiness", "readback_version": data["version"],
                          "as_of": data["as_of"], "agent": {k: a.get(k) for k in (
                              "license", "independent_events", "realized_contribution_usd",
                              "lower_bound_incremental_value_per_event_usd", "exact_blocker",
                              "vs_hold_to_settlement_usd", "vs_immediate_exit_usd", "challenges", "blocked")},
                          "holdout_id": holdout, "trial_budget": HOLDOUT_BUDGET,
                          "separation": {"proposer": PROPOSER, "evaluator": "a separate evaluator, recorded via "
                                         "improvement.record_evaluation", "approver": "a named person "
                                         "(REQUIRES_APPROVAL)"}},
                affected_behavior=behaviour,
                training_boundary={"end": at, "basis": "evidence settled before the proposal"},
                evaluation_boundary={"start": at, "end": at + EVAL_WINDOW_S, "holdout_id": holdout},
                now=at)
        (out["proposed"] if res.get("ok") else out["refused"]).append(res.get("candidate_id") or res)
    return out
