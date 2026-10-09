"""THE CANDIDATE-REVIEW WORKFLOW: ONE DEREK CANDIDATE, SEVEN GROUNDED STEPS.

  1 DEREK_CANDIDATE           Derek's paper ENTER decision
  2 KAREN_CHALLENGE           Karen's challenge of it (karen_challenges)
  3 SCOUT_EVIDENCE            Scout's feature observations of its game
  4 ARCHER_EXECUTION_ESTIMATE  Archer's SHADOW execution estimate
  5 ALLOCATOR_RANKING         the Chief Allocator's shadow rank (intel_*)
  6 AUDREY_RISK_CHECK         Audrey's independent risk recompute (intel_*)
  7 XAVIER_MANAGEMENT_PLAN    Xavier's review of the filled position

Each step is persisted once, in order (migration 217:
pos_candidate_review_steps), with the QUESTION asked of that agent, the
AGENT whose record answers it, the EVIDENCE ids (grounded: each exists), the
RESPONSE (quoted from that record), any DISAGREEMENT with an earlier step,
the RESOLUTION, the EXPERIMENT it feeds (a collaboration-loop finding or a
Scout tournament) and, later and once, the RESULT (Archer's realized
execution loss; Xavier's first review).

WHAT THIS IS NOT. Nobody is asked to act and nothing is decided here: every
response is read from that agent's own record. A step with no record says
NO_RECORD (or NOT_APPLICABLE: Xavier manages only filled positions) and
cites the candidate. A disagreement is recorded, never resolved by
authority: Archer and Scout hold none, and nothing is blocked, approved or
sent. The writer is the workflow (recorded_by POS_WORKFLOW), hosted by
Archer's runner, inside a transaction declared as ARCHER -- so the database
would refuse it any order / approval / control write.

THE COLLABORATION LOOP. When Archer's SHADOW recommendation disagrees with
Derek's ENTER (SKIP_EXECUTION / WAIT), Archer opens a loop finding (203)
with its EVIDENCE and HYPOTHESIS stages, routed to his default peers
(collaboration_loop.PEER_ROUTING); the step's experiment_ref names it.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

from . import collaboration_loop as CL
from . import pos_authority as PA
from . import pos_evidence as _PE  # noqa: F401
from . import registry as R

STEPS = (
    (1, "DEREK_CANDIDATE", "DEREK"),
    (2, "KAREN_CHALLENGE", "KAREN"),
    (3, "SCOUT_EVIDENCE", "SCOUT"),
    (4, "ARCHER_EXECUTION_ESTIMATE", "ARCHER"),
    (5, "ALLOCATOR_RANKING", "CHIEF_ALLOCATOR"),
    (6, "AUDREY_RISK_CHECK", "AUDREY"),
    (7, "XAVIER_MANAGEMENT_PLAN", "XAVIER"),
)
QUESTIONS = {
    "DEREK_CANDIDATE": ("What is the candidate, on what probability, and "
                        "what does Derek propose?"),
    "KAREN_CHALLENGE": "Is there a grounded challenge to this candidate?",
    "SCOUT_EVIDENCE": ("Is there compliant external evidence about this "
                       "game, and what is its status?"),
    "ARCHER_EXECUTION_ESTIMATE": ("How much of the theoretical edge survives "
                                 "execution, and should it execute?"),
    "ALLOCATOR_RANKING": ("Where does the Chief Allocator rank it, and with "
                          "what shadow size?"),
    "AUDREY_RISK_CHECK": ("Does Audrey's independent risk recompute agree "
                          "with the book?"),
    "XAVIER_MANAGEMENT_PLAN": ("Once filled, what is Xavier's management "
                               "plan?"),
}
MAX_REVIEWS_PER_PASS = 10
NO_AUTHORITY = ("SHADOW: no agent here holds authority over this "
                "candidate's execution; the disagreement is recorded for "
                "the outcome measurement and nothing is blocked, approved "
                "or sent.")


def review_id_for(decision_id: str) -> str:
    return "pcr:%s" % hashlib.sha256(str(decision_id).encode()).hexdigest()[:24]


def _ep(v):
    return v.timestamp() if isinstance(v, datetime) else v


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _cand_ref(d: dict) -> list:
    return [{"kind": "paper_decisions", "id": str(d["decision_id"])}]


async def _exists(conn, table: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", table) is not None


# ═════════════════════════════════════════════════════════════════════
# EACH STEP, FROM THAT AGENT'S OWN RECORD
# ═════════════════════════════════════════════════════════════════════

async def step_derek(conn, d: dict, ctx: dict) -> dict:
    p = d.get("p_blended") or d.get("p_pinnacle") or d.get("p_internal")
    return {"status": "ANSWERED", "evidence_refs": _cand_ref(d),
            "response": ("ENTER %s %s, qty %s at <= %s; probability %s; "
                         "strategy %s, policy %s." % (
                             d.get("us_market_slug"), d.get("holding_side"),
                             d.get("proposed_qty"), d.get("limit_price"), p,
                             d.get("strategy"), d.get("policy_version")))}


async def step_karen(conn, d: dict, ctx: dict) -> dict:
    if not await _exists(conn, "karen_challenges"):
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "Karen's challenge records are not present in "
                            "this database (migration 207)."}
    c = await conn.fetchrow(
        "SELECT challenge_id, state, claim, response_stance, outcome, "
        "       severity FROM karen_challenges WHERE (target_kind = "
        "       'paper_decisions' AND target_id = $1) OR evidence_refs @> "
        "       $2::jsonb ORDER BY challenged_at DESC LIMIT 1",
        str(d["decision_id"]), json.dumps([{"kind": "paper_decisions",
                                            "id": str(d["decision_id"])}]))
    if c is None:
        run = await conn.fetchval(
            "SELECT run_id FROM agent_runs WHERE agent_id='KAREN' "
            " ORDER BY started_at DESC LIMIT 1")
        refs = _cand_ref(d) + ([{"kind": "agent_runs", "id": run}]
                               if run else [])
        return {"status": "NO_RECORD", "evidence_refs": refs,
                "response": ("Karen has raised no challenge citing this "
                             "decision%s." % (" (her latest detector run: %s)"
                                              % run if run else
                                              "; her runner has not run"))}
    ctx["karen"] = dict(c)
    dis = None
    if c["state"] in ("OPEN", "RESPONDED", "UPHELD"):
        dis = {"between": ["KAREN", "DEREK"], "karen": c["claim"][:500],
               "derek": c["response_stance"] or "NOT_YET_ANSWERED"}
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "karen_challenges",
                               "id": c["challenge_id"]}] + _cand_ref(d),
            "response": "%s (%s): %s" % (c["state"], c["severity"],
                                         c["claim"][:1200]),
            "disagreement": dis,
            "resolution": (("Outcome %s, recorded by an independent "
                            "evaluator." % c["outcome"]) if c["outcome"]
                           else "Awaiting the peer response and the "
                                "independent evaluation.")}


async def step_scout(conn, d: dict, ctx: dict) -> dict:
    if not await _exists(conn, "external_valuations"):
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "No game identity links this market to a game."}
    rows = await conn.fetch(
        "SELECT o.observation_id, f.feature, f.state, o.value, "
        "       o.value_label, o.confidence, o.freshness_s, "
        "       o.licensing_class, t.tournament_id "
        "  FROM scout_feature_observations o "
        "  JOIN scout_features f USING (feature_id) "
        "  LEFT JOIN scout_feature_tournaments t USING (feature_id) "
        " WHERE o.event_key IN (SELECT DISTINCT condition_id FROM "
        "       external_valuations WHERE us_market_slug = $1 AND "
        "       condition_id IS NOT NULL) "
        " ORDER BY o.source_timestamp DESC LIMIT 5",
        d.get("us_market_slug"))
    if not rows:
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": ("Scout holds no compliant feature observation "
                             "for this market's game.")}
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "scout_feature_observations",
                               "id": r["observation_id"]} for r in rows],
            "response": "; ".join(
                "%s = %s (%s; confidence %s; freshness %ss; %s; feature %s)"
                % (r["feature"], r["value"], r["value_label"],
                   r["confidence"], round(float(r["freshness_s"])),
                   r["licensing_class"], r["state"]) for r in rows)
            + ". A feature under test informs no decision until the "
              "evaluator validates it.",
            "experiment_ref": {"kind": "scout_feature_tournaments",
                               "ids": sorted({r["tournament_id"] for r in rows
                                              if r["tournament_id"]})}}


async def _open_disagreement_finding(conn, d: dict, e: dict, *,
                                     now: float) -> str | None:
    """Archer's loop finding for a Derek-ENTER / Archer-not-execute
    disagreement: EVIDENCE + HYPOTHESIS, idempotent."""
    refs = [{"kind": "eddie_execution_estimates", "id": e["estimate_id"]},
            {"kind": "paper_decisions", "id": str(d["decision_id"])}]
    title = "Execution disagreement on %s" % d["decision_id"]
    got = await CL.open_finding(
        conn, proposer=R.ARCHER, title=title[:300],
        statement=("Derek recorded ENTER; Archer's SHADOW estimate %s "
                   "recommends %s (%s). Routed to %s." % (
                       e["estimate_id"], e["recommendation"],
                       e["recommendation_reason"][:400],
                       ", ".join(CL.PEER_ROUTING["ARCHER"]))),
        evidence_refs=refs, evidence_window_end=now, at=now)
    if not got.get("ok"):
        return None
    if await conn.fetchval("SELECT stage FROM agent_findings WHERE "
                           " finding_id=$1", got["finding_id"]) == CL.EVIDENCE:
        await CL.record_hypothesis(
            conn, got["finding_id"], actor=R.ARCHER,
            hypothesis=("Executing %s loses more than its theoretical edge "
                        "(%s pp): expected net executable edge %s pp. The "
                        "realized execution loss will show it." % (
                            d["decision_id"], e["theoretical_edge_pp"],
                            e["expected_net_executable_edge_pp"])),
            evidence_refs=refs, at=now + 0.001)
    return got["finding_id"]


async def step_archer(conn, d: dict, ctx: dict) -> dict:
    e = await conn.fetchrow(
        "SELECT * FROM eddie_execution_estimates WHERE decision_id=$1 "
        " ORDER BY estimated_at DESC LIMIT 1", str(d["decision_id"]))
    if e is None:
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "Archer has not estimated this candidate."}
    e = dict(e)
    ctx["archer"] = e
    dis = res = exp = None
    if e["recommendation"] in ("SKIP_EXECUTION", "WAIT"):
        dis = {"between": ["DEREK", "ARCHER"], "derek": "ENTER",
               "archer": e["recommendation"],
               "why": e["recommendation_reason"][:500]}
        res = NO_AUTHORITY
        # a MEASURED negative (SKIP) is a hypothesis worth a loop finding;
        # an unmeasured WAIT is not
        fid = await _open_disagreement_finding(
            conn, d, e, now=ctx["now"]) \
            if e["recommendation"] == "SKIP_EXECUTION" else None
        if fid:
            exp = {"kind": "agent_findings", "id": fid,
                   "routed_to": list(CL.PEER_ROUTING["ARCHER"])}
    un = _j(e.get("unmeasured")) or {}
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "eddie_execution_estimates",
                               "id": e["estimate_id"]}],
            "response": ("%s (SHADOW) as %s: theoretical edge %s pp, "
                         "expected execution loss %s pp, net executable "
                         "edge %s pp, fill probability %s, EV %s USD. %s%s" % (
                             e["recommendation"], e["execution_style"],
                             e["theoretical_edge_pp"],
                             e["expected_execution_loss_pp"],
                             e["expected_net_executable_edge_pp"],
                             e["expected_fill_probability"],
                             e["expected_executable_ev_usd"],
                             e["recommendation_reason"][:600],
                             (" Unmeasured: %s." % ", ".join(sorted(un)))
                             if un else "")),
            "disagreement": dis, "resolution": res, "experiment_ref": exp}


async def step_allocator(conn, d: dict, ctx: dict) -> dict:
    if not await _exists(conn, "intel_allocations"):
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "The shadow allocator's records are not present "
                            "(migration 208)."}
    a = await conn.fetchrow(
        "SELECT candidate_id, run_id, rank, shadow_usd, net_ev_per_dollar, "
        "       binding_constraint FROM intel_allocations "
        " WHERE candidate_id = $1 ORDER BY computed_at DESC LIMIT 1",
        "decision:%s" % d["decision_id"])
    if a is None:
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "The Chief Allocator has not ranked this "
                            "candidate."}
    dis = None
    e = ctx.get("archer") or {}
    if e.get("recommendation") in ("SKIP_EXECUTION", "WAIT") and \
            float(a["shadow_usd"] or 0) > 0:
        dis = {"between": ["ARCHER", "CHIEF_ALLOCATOR"],
               "archer": e.get("recommendation"),
               "allocator_shadow_usd": float(a["shadow_usd"])}
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "intel_allocations",
                               "id": a["candidate_id"]}],
            "response": ("Rank %s in run %s: shadow %s USD, net EV per "
                         "dollar %s, binding constraint %s." % (
                             a["rank"], a["run_id"], a["shadow_usd"],
                             a["net_ev_per_dollar"],
                             a["binding_constraint"])),
            "disagreement": dis,
            "resolution": NO_AUTHORITY if dis else None}


async def step_audrey(conn, d: dict, ctx: dict) -> dict:
    if not await _exists(conn, "intel_audrey_risk_checks"):
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "Audrey's risk recompute records are not "
                            "present (migration 208)."}
    rows = await conn.fetch(
        "SELECT run_id, book, metric, primary_value, audrey_value, agrees "
        "  FROM intel_audrey_risk_checks WHERE run_id = (SELECT run_id FROM "
        "       intel_audrey_risk_checks ORDER BY computed_at DESC LIMIT 1)")
    if not rows:
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "Audrey has recorded no risk recompute."}
    bad = [r for r in rows if r["agrees"] is False]
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "intel_audrey_risk_checks",
                               "id": "%s|%s|%s" % (r["run_id"], r["book"],
                                                   r["metric"])}
                              for r in rows[:10]],
            "response": ("Book-level recompute run %s: %d metric(s), %d "
                         "disagree%s." % (rows[0]["run_id"], len(rows),
                                          len(bad), (" (%s)" % ", ".join(
                                              r["metric"] for r in bad[:5]))
                                          if bad else "")),
            "disagreement": ({"between": ["AUDREY", "CHIEF_ALLOCATOR"],
                              "metrics": [r["metric"] for r in bad[:10]]}
                             if bad else None)}


async def step_xavier(conn, d: dict, ctx: dict) -> dict:
    g = await conn.fetchrow(
        "SELECT o.group_id, bool_or(f.fill_id IS NOT NULL) AS filled "
        "  FROM paper_orders o LEFT JOIN paper_fills f USING (order_id) "
        " WHERE o.decision_id = $1 GROUP BY o.group_id LIMIT 1",
        str(d["decision_id"]))
    if g is None or not g["filled"]:
        return {"status": "NOT_APPLICABLE", "evidence_refs": _cand_ref(d),
                "response": ("No confirmed fill yet: Xavier manages only "
                             "filled positions.")}
    r = await conn.fetchrow(
        "SELECT review_id, recommendation, refusal FROM paper_xavier_reviews"
        " WHERE group_id = $1 ORDER BY reviewed_at DESC LIMIT 1",
        g["group_id"])
    if r is None:
        return {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                "response": "Filled (group %s); Xavier has not reviewed it "
                            "yet." % g["group_id"]}
    return {"status": "ANSWERED",
            "evidence_refs": [{"kind": "paper_xavier_reviews",
                               "id": r["review_id"]}],
            "response": "Review %s of group %s: %s%s." % (
                r["review_id"], g["group_id"], r["recommendation"],
                (" (refusal %s)" % r["refusal"]) if r["refusal"] else "")}


STEP_FN = {"DEREK_CANDIDATE": step_derek, "KAREN_CHALLENGE": step_karen,
           "SCOUT_EVIDENCE": step_scout,
           "ARCHER_EXECUTION_ESTIMATE": step_archer,
           "ALLOCATOR_RANKING": step_allocator,
           "AUDREY_RISK_CHECK": step_audrey,
           "XAVIER_MANAGEMENT_PLAN": step_xavier}


# ═════════════════════════════════════════════════════════════════════
# ASSEMBLY
# ═════════════════════════════════════════════════════════════════════

async def assemble(conn, decision_id: str, *, now: float | None = None
                   ) -> dict:
    """Record every missing step of one candidate's review, in order, in
    one transaction declared as ARCHER. Idempotent. Never raises."""
    at = float(now if now is not None else time.time())
    PA.assert_may(R.ARCHER, "write.candidate_reviews")
    try:
        async with conn.transaction():
            await PA.act_as(conn, R.ARCHER)
            d = await conn.fetchrow(
                "SELECT decision_id, decided_at, us_market_slug, "
                "       holding_side, proposed_qty, limit_price, p_blended, "
                "       p_pinnacle, p_internal, strategy, policy_version, "
                "       verdict FROM paper_decisions WHERE decision_id=$1",
                str(decision_id))
            if d is None or d["verdict"] != "ENTER":
                return {"ok": False, "refusal": "NOT_A_DEREK_CANDIDATE"}
            d = dict(d)
            rid = review_id_for(d["decision_id"])
            await conn.execute(
                "INSERT INTO pos_candidate_reviews (review_id, decision_id, "
                " opened_at) VALUES ($1,$2,to_timestamp($3)) "
                "ON CONFLICT DO NOTHING", rid, d["decision_id"], at)
            have = await conn.fetchval(
                "SELECT steps_recorded FROM pos_candidate_reviews "
                " WHERE review_id=$1 FOR UPDATE", rid)
            ctx = {"now": at}
            written = []
            for seq, step, agent in STEPS:
                if seq <= have:
                    if step == "ARCHER_EXECUTION_ESTIMATE":
                        e = await conn.fetchrow(
                            "SELECT * FROM eddie_execution_estimates WHERE "
                            " decision_id=$1 ORDER BY estimated_at DESC "
                            " LIMIT 1", d["decision_id"])
                        ctx["archer"] = dict(e) if e else {}
                    continue
                s = await STEP_FN[step](conn, d, ctx)
                n = CL.normalise_refs(s["evidence_refs"])
                v = await CL.verify_refs_exist(conn, n["refs"]) \
                    if n.get("ok") else n
                if not v.get("ok"):
                    s = {"status": "NO_RECORD", "evidence_refs": _cand_ref(d),
                         "response": "The cited record could not be "
                                     "verified (%s); nothing is claimed."
                                     % v.get("refusal")}
                await conn.execute(
                    "INSERT INTO pos_candidate_review_steps (review_id, seq, "
                    " step, agent, question, status, evidence_refs, response,"
                    " disagreement, resolution, experiment_ref, at) VALUES "
                    " ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10,"
                    " $11::jsonb,to_timestamp($12))", rid, seq, step, agent,
                    QUESTIONS[step], s["status"],
                    json.dumps(s["evidence_refs"]), s["response"][:4000],
                    None if s.get("disagreement") is None
                    else json.dumps(s["disagreement"], default=str),
                    s.get("resolution"),
                    None if s.get("experiment_ref") is None
                    else json.dumps(s["experiment_ref"], default=str), at)
                written.append(step)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "THE_DATABASE_REFUSED_THE_REVIEW",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return {"ok": True, "review_id": rid, "written": written,
            "production_effect": "NONE"}


async def attach_results(conn, *, now: float, limit: int = 20) -> dict:
    """THE RESULTS, once each: Archer's realized execution loss on step 4,
    Xavier's first review on step 7."""
    n = 0
    async with conn.transaction():
        await PA.act_as(conn, R.ARCHER)
        for r in await conn.fetch(
                "SELECT s.review_id, x.outcome_id, "
                "       x.predicted_execution_loss_pp, "
                "       x.realized_execution_loss_pp "
                "  FROM pos_candidate_review_steps s "
                "  JOIN pos_candidate_reviews v USING (review_id) "
                "  JOIN eddie_execution_outcomes x ON x.decision_id = "
                "       v.decision_id "
                " WHERE s.seq = 4 AND s.result IS NULL "
                # (266) a step written under the historical alias EDDIE is
                # a historical record: never updated (frozen in the DB too)
                "   AND s.agent <> ALL($2::text[]) LIMIT $1", limit,
                sorted(R.HISTORICAL_ALIASES)):
            await conn.execute(
                "UPDATE pos_candidate_review_steps SET result=$2::jsonb, "
                " result_at=to_timestamp($3) WHERE review_id=$1 AND seq=4 "
                "   AND result IS NULL", r["review_id"], json.dumps({
                    "kind": "eddie_execution_outcomes",
                    "id": r["outcome_id"],
                    "predicted_execution_loss_pp":
                        r["predicted_execution_loss_pp"],
                    "realized_execution_loss_pp":
                        r["realized_execution_loss_pp"]}), float(now))
            n += 1
        # XAVIER'S FIRST REVIEW, ONE INDEX PROBE PER ORDER GROUP. The first
        # review of each of the decision's order groups (paper_xavier_
        # reviews_group_idx), then the earliest of those -- the same row as
        # "the earliest review joined to any of its orders". Written as one
        # join ORDER BY reviewed_at LIMIT 1, the planner walked the whole
        # reviewed_at index (163k reviews, 1.6 GB in production) for EVERY
        # step-7 row whose decision has no order and therefore no match:
        # research-sql 37936489884 shows that plan, and the phase's 25 s
        # bound (archer_runner.PHASE_TIMEOUT_S) was hit on 265 of 265 Archer
        # passes in 24 h (run 37936367236; TimeoutError since 2026-10-05
        # 22:25), so no step-4 / step-7 result was attached since.
        for r in await conn.fetch(
                "SELECT s.review_id, x.review_id AS xr, x.recommendation "
                "  FROM pos_candidate_review_steps s "
                "  JOIN pos_candidate_reviews v USING (review_id) "
                "  JOIN LATERAL (SELECT f.review_id, f.recommendation "
                "                  FROM paper_orders o "
                "                  CROSS JOIN LATERAL (SELECT xr.review_id, "
                "                         xr.recommendation, xr.reviewed_at "
                "                    FROM paper_xavier_reviews xr "
                "                   WHERE xr.group_id = o.group_id "
                "                   ORDER BY xr.reviewed_at LIMIT 1) f "
                "                 WHERE o.decision_id = v.decision_id "
                "                 ORDER BY f.reviewed_at LIMIT 1) x "
                "       ON true WHERE s.seq = 7 AND s.result IS NULL "
                "   AND s.status <> 'ANSWERED' LIMIT $1", limit):
            await conn.execute(
                "UPDATE pos_candidate_review_steps SET result=$2::jsonb, "
                " result_at=to_timestamp($3) WHERE review_id=$1 AND seq=7 "
                "   AND result IS NULL", r["review_id"], json.dumps({
                    "kind": "paper_xavier_reviews", "id": r["xr"],
                    "recommendation": r["recommendation"]}), float(now))
            n += 1
    return {"attached": n}


async def reviews(conn, *, limit: int = 20) -> list:
    rows = await conn.fetch(
        "SELECT * FROM pos_candidate_reviews ORDER BY opened_at DESC, "
        " review_id LIMIT $1", max(1, min(int(limit), 200)))
    out = []
    for r in rows:
        steps = await conn.fetch(
            "SELECT * FROM pos_candidate_review_steps WHERE review_id=$1 "
            " ORDER BY seq", r["review_id"])
        out.append({"review_id": r["review_id"],
                    "decision_id": r["decision_id"],
                    "opened_at": _ep(r["opened_at"]),
                    "steps_recorded": r["steps_recorded"],
                    "steps": [{k: (_j(v) if k in (
                        "evidence_refs", "disagreement", "experiment_ref",
                        "result") else _ep(v)) for k, v in dict(s).items()}
                        for s in steps],
                    "evidence": [{"kind": "pos_candidate_reviews",
                                  "id": r["review_id"], "href": None}]})
    return out
