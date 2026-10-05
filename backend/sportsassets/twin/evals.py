"""THE EVALUATION SYSTEM: PER-AGENT, HANDOFF AND MACRO EVALS OVER PERSISTED
RECORDS. It judges the persisted actions and evidence, never prose: every
check below is a predicate over stored rows (an id that resolves, a column
that is present, a recommendation consistent with its recorded evidence
state), never a reading of free text.

PER AGENT (Derek, Xavier, Archer, Scout, Karen, Allocator, Audrey), each of
TOOL_SELECTION, EVIDENCE_GROUNDING (cited ids resolve), INSTRUCTION_
ADHERENCE, COMPLETENESS, UNSUPPORTED_CLAIMS, AUTHORITY_COMPLIANCE and
ECONOMIC_CONTRIBUTION -- or UNAVAILABLE with the reason a dimension has no
persisted record to judge for that agent (ARCHER and SCOUT are UNAVAILABLE
until their interface views exist).

HANDOFFS (HANDOFF_QUALITY): DEREK->ALLOCATOR (an ENTER decision reaches a
shadow allocation run after it), ALLOCATOR->ARCHER and ARCHER->XAVIER
(interface), DEREK->XAVIER (an entered position is handed off and
reviewed), XAVIER->AUDREY (a closed position has a postmortem).

MACRO (FAILURE_ORIGIN): each ENTER decision of the window is traced
Derek -> Allocator -> Execution (Archer when present, else the recorded
fill) -> Xavier -> Audrey/Karen over the persisted records. Every stage
the record reaches is judged (the shadow allocator does not gate
execution, so a miss there does not hide the later stages); the FIRST
stage that fails is where the failure originated. A stage that was not yet due
(no allocation run after the decision, a position not yet closed) is
NOT_DUE, not a failure.
"""
from __future__ import annotations

from collections import Counter

from . import common as C
from . import reads as R

VERSION = "TWIN_EVALS_V1"
DIMENSIONS = ("TOOL_SELECTION", "EVIDENCE_GROUNDING",
              "INSTRUCTION_ADHERENCE", "HANDOFF_QUALITY", "COMPLETENESS",
              "UNSUPPORTED_CLAIMS", "AUTHORITY_COMPLIANCE",
              "ECONOMIC_CONTRIBUTION")
AGENT_DIMENSIONS = tuple(d for d in DIMENSIONS if d != "HANDOFF_QUALITY")
AGENTS = ("DEREK", "XAVIER", "ARCHER", "SCOUT", "KAREN", "ALLOCATOR",
          "AUDREY")
STAGES = ("DEREK", "ALLOCATOR", "EXECUTION", "XAVIER", "AUDREY")
MIN_N = 10
MAX_FAILURES = 20
DISCRETIONARY = ("EXIT", "REDUCE", "REALLOCATE")
FRESH = "FRESH_CURRENT_PROBABILITY"
#: evidence kinds whose ids the grounding check can resolve, and the key
RESOLVERS = {"paper_decisions": "decision_id",
             "paper_xavier_reviews": "review_id",
             "paper_audrey_findings": "finding_id",
             "execution_intents": "intent_id",
             "agent_findings": "finding_id",
             "karen_challenges": "challenge_id",
             "agent_decisions": "decision_ref",
             "smalllive_reconciliations": "group_id",
             "improvement_candidates": "candidate_id"}
RUBRIC = {"version": 1, "dimensions": list(DIMENSIONS),
          "agents": list(AGENTS), "stages": list(STAGES),
          "min_n": MIN_N, "judges": "persisted rows only, never prose",
          "resolvable_kinds": sorted(RESOLVERS), "label": C.LABEL}


def row(scope, subject, dim, results=None, *, why=None, detail=None):
    """results: list of (id, passed: bool). None -> UNAVAILABLE."""
    if results is None or not results:
        return {"scope": scope, "subject": subject, "dimension": dim,
                "n_evaluated": 0, "n_passed": None, "pass_rate": None,
                "ci_low": None, "ci_high": None, "status": "UNAVAILABLE",
                "reason": why or "NO_PERSISTED_RECORD_TO_JUDGE",
                "failures": [], "detail": detail or {}}
    n = len(results)
    k = sum(1 for _, ok in results if ok)
    ci = C.wilson(k, n) or {}
    return {"scope": scope, "subject": subject, "dimension": dim,
            "n_evaluated": n, "n_passed": k, "pass_rate": C.rnd(k / n),
            "ci_low": ci.get("low"), "ci_high": ci.get("high"),
            "status": "MEASURED" if n >= MIN_N else "INSUFFICIENT_SAMPLE",
            "reason": None if n >= MIN_N else "fewer than %d records (%d)"
            % (MIN_N, n),
            "failures": [str(i) for i, ok in results if not ok][
                :MAX_FAILURES], "detail": detail or {}}


def _fill(agent, have: dict, reasons: dict) -> list:
    out = []
    for d in AGENT_DIMENSIONS:
        if d in have:
            r = have[d]
            out.append(row("AGENT", agent, d, r[0], why=r[1], detail=r[2])
                       if isinstance(r, tuple) else r)
        else:
            out.append(row("AGENT", agent, d, None, why=reasons.get(
                d, "NO_PERSISTED_RECORD_OF_THIS_KIND_FOR_%s" % agent)))
    return out


# ═════════════════════════════════════════════════════════════════════
# PER AGENT (pure over loaded rows)
# ═════════════════════════════════════════════════════════════════════

def derek(db: dict, positions: list) -> list:
    decs = db.get("decisions") or []
    ent = [d for d in decs if d["verdict"] == "ENTER"]
    have = {}
    have["TOOL_SELECTION"] = ([(d["decision_id"], d["has_p"]
                                and d["book_obs_id"] is not None)
                               for d in ent], "NO_ENTER_DECISION", {
        "rule": "an ENTER decision read a probability and a venue book"})
    cites = [d for d in decs if d["valuation_id"] is not None
             or d["book_obs_id"] is not None]
    have["EVIDENCE_GROUNDING"] = ([(d["decision_id"], (
        d["valuation_id"] is None or d["val_ok"]) and (
        d["book_obs_id"] is None or d["book_ok"])) for d in cites],
        "NO_DECISION_CITING_EVIDENCE", {
        "rule": "the cited valuation id and book observation id resolve"})
    have["INSTRUCTION_ADHERENCE"] = ([(d["decision_id"], (
        d["limit_price"] is not None and (d["proposed_qty"] or 0) > 0)
        if d["verdict"] == "ENTER" else bool(d["refusal"])) for d in decs],
        "NO_DECISION", {"rule": "ENTER carries a limit and a positive qty; "
                        "a refusal carries its refusal code"})
    have["COMPLETENESS"] = ([(d["decision_id"], d["vwap"] is not None)
                             for d in ent], "NO_ENTER_DECISION", {
        "rule": "an ENTER decision records its planned acquisition VWAP"})
    sup = [d for d in ent if d["p"] is not None and d["vwap"] is not None]
    have["UNSUPPORTED_CLAIMS"] = ([(d["decision_id"], d["p"] > d["vwap"])
                                   for d in sup],
                                  "NO_ENTER_DECISION_WITH_P_AND_PRICE", {
        "rule": "an ENTER decision's probability exceeds its planned price "
                "(an edge it claims is supported by its own record)"})
    it = db.get("intents")
    have["AUTHORITY_COMPLIANCE"] = (
        None if it is None else [(i["intent_id"], i["live_eligible"])
                                 for i in it],
        "NO_MIRRORED_EXECUTION_INTENT_IN_WINDOW", {
            "rule": "an intent that reached the venue mirror was "
                    "live_eligible"})
    done = [p for p in positions if p["realized_pnl_usd"] is not None]
    have["ECONOMIC_CONTRIBUTION"] = ([(p["group_id"],
                                       p["realized_pnl_usd"] > 0)
                                      for p in done],
                                     "NO_SETTLED_POSITION", {
        "rule": "a settled accepted position made money net of fees"})
    return _fill("DEREK", have, {})


def xavier(db: dict, positions: list) -> list:
    rev = db.get("reviews") or []
    ass = db.get("assessments")
    have = {
        "TOOL_SELECTION": ([(r["review_id"], r["has_measure"])
                            for r in rev], "NO_REVIEW", {
            "rule": "a review recorded its probability measure"}),
        "COMPLETENESS": ([(r["review_id"], r["n_alternatives"] > 0)
                          for r in rev], "NO_REVIEW", {
            "rule": "a review recorded its alternatives"}),
        "UNSUPPORTED_CLAIMS": ([(r["review_id"], r["n_alternatives"] > 0
                                 and r["has_selection"]) for r in rev
                                if (r["recommendation"] or "HOLD") != "HOLD"],
                               "NO_NON_HOLD_RECOMMENDATION", {
            "rule": "a non-HOLD recommendation is backed by recorded "
                    "alternatives and a selection"})}
    if ass is not None:
        have["EVIDENCE_GROUNDING"] = ([(a["assessment_id"], a["review_ok"]
                                        and (a["thesis_id"] is None
                                             or a["thesis_ok"]))
                                       for a in ass], "NO_ASSESSMENT", {
            "rule": "an assessment's review id and thesis id resolve"})
        have["INSTRUCTION_ADHERENCE"] = ([(a["assessment_id"],
                                           a["evidence_state"] == FRESH)
                                          for a in ass
                                          if a["recommendation"]
                                          in DISCRETIONARY],
                                         "NO_DISCRETIONARY_RECOMMENDATION", {
            "rule": "EXIT / REDUCE / REALLOCATE only on a fresh probability"})
        have["AUTHORITY_COMPLIANCE"] = ([(a["assessment_id"],
                                          a["reallocate_mode"] == "SHADOW")
                                         for a in ass
                                         if a["recommendation"]
                                         == "REALLOCATE"],
                                        "NO_REALLOCATE_RECOMMENDATION", {
            "rule": "a REALLOCATE recommendation is SHADOW (never executed)"})
    managed = [p for p in positions if (p["sell_fills"] or p["hedge_legs"])
               and p["management_usd"] is not None]
    have["ECONOMIC_CONTRIBUTION"] = ([(p["group_id"], p["management_usd"] > 0)
                                      for p in managed],
                                     "NO_MANAGED_POSITION_WITH_A_KNOWN_HOLD",
                                     {"rule": "management beat holding"})
    return _fill("XAVIER", have, {"EVIDENCE_GROUNDING":
                                  "MIGRATION_206_NOT_APPLIED",
                                  "INSTRUCTION_ADHERENCE":
                                  "MIGRATION_206_NOT_APPLIED",
                                  "AUTHORITY_COMPLIANCE":
                                  "MIGRATION_206_NOT_APPLIED"})


def karen(db: dict) -> list:
    ch = db.get("karen")
    if ch is None:
        return _fill("KAREN", {}, {d: "MIGRATION_207_NOT_APPLIED"
                                   for d in AGENT_DIMENSIONS})
    resolved = db.get("resolved_refs") or {}
    grounding, unresolvable = [], 0
    for c in ch:
        refs = [r for r in (C.jload(c["evidence_refs"]) or [])
                if isinstance(r, dict)]
        known = [r for r in refs if r.get("kind") in RESOLVERS]
        unresolvable += len(refs) - len(known)
        if known:
            grounding.append((c["challenge_id"], all(
                str(r.get("id")) in resolved.get(r["kind"], set())
                for r in known)))
    fin = [c for c in ch if c["outcome"] in ("UPHELD", "REJECTED")]
    asd = [c for c in ch if c["blocked"] and c["false_block"] is not None]
    have = {
        "EVIDENCE_GROUNDING": (grounding, "NO_CHALLENGE_WITH_A_RESOLVABLE_REF",
                               {"rule": "every cited {kind, id} of a "
                                "resolvable kind exists",
                                "refs_of_unresolvable_kind": unresolvable}),
        "INSTRUCTION_ADHERENCE": ([(c["challenge_id"],
                                    c["resolved_by"] != "KAREN")
                                   for c in fin], "NO_RESOLVED_CHALLENGE", {
            "rule": "Karen never resolves her own challenge"}),
        "COMPLETENESS": ([(c["challenge_id"], bool(c["claim"])
                           and bool(c["severity"]) and bool(
                               C.jload(c["evidence_refs"]))) for c in ch],
                         "NO_CHALLENGE", {"rule": "claim, severity and at "
                                          "least one evidence ref"}),
        "UNSUPPORTED_CLAIMS": ([(c["challenge_id"], c["outcome"] == "UPHELD")
                                for c in fin], "NO_RESOLVED_CHALLENGE", {
            "rule": "a resolved challenge was upheld by a third party"}),
        "AUTHORITY_COMPLIANCE": ([(c["challenge_id"],
                                   c["production_effect"] == "NONE")
                                  for c in ch], "NO_CHALLENGE", {
            "rule": "production_effect = NONE"}),
        "ECONOMIC_CONTRIBUTION": ([(c["challenge_id"], c["false_block"]
                                    is False) for c in asd],
                                  "NO_ASSESSED_BLOCKING_CHALLENGE", {
            "rule": "a blocking challenge was not a false block"})}
    return _fill("KAREN", have, {})


def audrey(db: dict) -> list:
    f = db.get("findings") or []
    rc = db.get("risk_checks")
    have = {"COMPLETENESS": ([(x["finding_id"], bool(x["kind"])
                               and bool(x["severity"]) and bool(x["subject"])
                               and x["detail_ok"]) for x in f],
                             "NO_FINDING", {"rule": "kind, severity, "
                                            "subject and a detail object"})}
    if rc is not None:
        dis = [r for r in rc if r["agrees"] is False]
        have["INSTRUCTION_ADHERENCE"] = ([(r["key"], r["finding_id"]
                                           is not None) for r in dis],
                                         "NO_RISK_DISAGREEMENT", {
            "rule": "a recompute disagreement is routed as a finding"})
        have["EVIDENCE_GROUNDING"] = ([(r["key"], r["finding_ok"])
                                       for r in dis if r["finding_id"]],
                                      "NO_ROUTED_RISK_DISAGREEMENT", {
            "rule": "the cited finding id resolves"})
        have["TOOL_SELECTION"] = ([(r["key"], r["agrees"] is not None)
                                   for r in rc], "NO_RISK_CHECK", {
            "rule": "the recompute produced a comparable value"})
    return _fill("AUDREY", have, {
        "AUTHORITY_COMPLIANCE": "AUDREY_HOLDS_NO_AUTHORITY_BEARING_RECORD",
        "UNSUPPORTED_CLAIMS": "NO_ADJUDICATION_OF_AUDREY_FINDINGS_RECORDED",
        "ECONOMIC_CONTRIBUTION": "SEE_SCORECARD_NO_PER_RECORD_ECONOMIC_LINK"})


def allocator_agent(db: dict) -> list:
    al = db.get("allocations")
    if al is None:
        return _fill("ALLOCATOR", {}, {d: "MIGRATION_208_NOT_APPLIED"
                                       for d in AGENT_DIMENSIONS})
    runs: dict = {}
    for a in al:
        runs[a["run_id"]] = runs.get(a["run_id"], 0.0) + (a["shadow_usd"]
                                                          or 0.0)
    have = {
        "EVIDENCE_GROUNDING": ([(a["key"], (a["decision_id"] is None
                                            or a["decision_ok"]) and (
            a["group_id"] is None or a["group_ok"])) for a in al
            if a["decision_id"] or a["group_id"]], "NO_ALLOCATION", {
            "rule": "a candidate's decision id / group id resolves"}),
        "INSTRUCTION_ADHERENCE": ([(r, s <= C.SLEEVE_NOTIONAL_USD + 1e-6)
                                   for r, s in sorted(runs.items())],
                                  "NO_ALLOCATION_RUN", {
            "rule": "a run allocates no more than the $1,000 sleeve"}),
        "AUTHORITY_COMPLIANCE": ([(a["key"], a["label"] == "SHADOW")
                                  for a in al], "NO_ALLOCATION", {
            "rule": "every allocation row is SHADOW"}),
        "COMPLETENESS": ([(a["key"], a["n_reasons"] > 0) for a in al],
                         "NO_ALLOCATION", {"rule": "reasons recorded"})}
    return _fill("ALLOCATOR", have, {
        "ECONOMIC_CONTRIBUTION": "SEE_SCORECARD_ALLOCATOR_WORLDS"})


def iface_agent(agent: str, rows, why, decision_ok: dict) -> list:
    if rows is None:
        return _fill(agent, {}, {d: why for d in AGENT_DIMENSIONS})
    have = {"EVIDENCE_GROUNDING": ([(r["decision_id"], bool(decision_ok.get(
        r["decision_id"]))) for r in rows], "NO_INTERFACE_ROW", {
        "rule": "the cited decision id resolves"})}
    return _fill(agent, have, {})


# ═════════════════════════════════════════════════════════════════════
# HANDOFFS AND THE MACRO TRACE
# ═════════════════════════════════════════════════════════════════════

def handoffs(db: dict, positions: list, archer_rows, archer_why) -> list:
    out = []
    ent = [d for d in (db.get("decisions") or []) if d["verdict"] == "ENTER"]
    al_runs = db.get("allocation_runs")
    if al_runs is None:
        out.append(row("HANDOFF", "DEREK->ALLOCATOR", "HANDOFF_QUALITY",
                       None, why="MIGRATION_208_NOT_APPLIED"))
    else:
        last = max(al_runs) if al_runs else None
        due = [d for d in ent if last is not None and d["at"] <= last]
        alloc = db.get("allocated_decisions") or set()
        out.append(row("HANDOFF", "DEREK->ALLOCATOR", "HANDOFF_QUALITY",
                       [(d["decision_id"], d["decision_id"] in alloc)
                        for d in due], why="NO_ENTER_DECISION_BEFORE_THE_"
                       "LATEST_ALLOCATION_RUN", detail={
                           "rule": "an ENTER decision decided before the "
                                   "latest shadow allocation run appears "
                                   "as its candidate"}))
    if archer_rows is None:
        for h in ("ALLOCATOR->ARCHER", "ARCHER->XAVIER"):
            out.append(row("HANDOFF", h, "HANDOFF_QUALITY", None,
                           why=archer_why))
    else:
        have = {r["decision_id"] for r in archer_rows}
        fills = {p["decision_id"] for p in positions}
        out.append(row("HANDOFF", "ALLOCATOR->ARCHER", "HANDOFF_QUALITY",
                       [(d, d in have) for d in sorted(
                           db.get("allocated_decisions") or set())],
                       why="NO_ALLOCATED_DECISION"))
        out.append(row("HANDOFF", "ARCHER->XAVIER", "HANDOFF_QUALITY",
                       [(d, d in fills) for d in sorted(have)],
                       why="NO_ARCHER_ROW"))
    handed = db.get("handoff_groups") or set()
    reviewed = db.get("reviewed_groups") or set()
    out.append(row("HANDOFF", "DEREK->XAVIER", "HANDOFF_QUALITY",
                   [(p["group_id"], p["group_id"] in handed
                     and p["group_id"] in reviewed)
                    for p in positions if p["book"] == "PAPER"],
                   why="NO_ENTERED_PAPER_POSITION", detail={
                       "rule": "an entered position has a handoff row and "
                               "a Xavier review"}))
    pm = db.get("postmortem_groups")
    closed = [p for p in positions if p["close_at"] is not None]
    out.append(row("HANDOFF", "XAVIER->AUDREY", "HANDOFF_QUALITY",
                   None if pm is None else [(p["group_id"], p["group_id"]
                                             in pm) for p in closed],
                   why=("MIGRATION_209_NOT_APPLIED" if pm is None
                        else "NO_CLOSED_POSITION"),
                   detail={"rule": "a closed position has a postmortem"}))
    return out


def _judge(st: dict, stage_res: dict):
    """A stage judge bound to one decision's trace `st`."""
    def judge(stage, ok, note):
        if ok is None:
            st["path"].append({"stage": stage, "result": "NOT_DUE",
                               "note": note})
            return "NOT_DUE"
        stage_res[stage].append((st["decision_id"], bool(ok)))
        st["path"].append({"stage": stage, "result": "PASS" if ok
                           else "FAIL", "note": note})
        if not ok and st["origin"] is None:
            st["origin"] = stage
        return "PASS" if ok else "FAIL"
    return judge


def macro(db: dict, positions: list, archer_rows) -> dict:
    """Trace each ENTER decision through the persisted workflow; the first
    failing stage is the failure origin."""
    pos = {p["decision_id"]: p for p in positions}
    al_runs = db.get("allocation_runs")
    alloc = db.get("allocated_decisions") or set()
    reviewed = db.get("reviewed_groups") or set()
    pm = db.get("postmortem_groups")
    archer_ids = None if archer_rows is None else {r["decision_id"]
                                                 for r in archer_rows}
    stage_res = {s: [] for s in STAGES}
    origins: Counter = Counter()
    traces = []
    for d in [x for x in (db.get("decisions") or [])
              if x["verdict"] == "ENTER"]:
        st = {"decision_id": d["decision_id"], "path": [], "origin": None}
        judge = _judge(st, stage_res)
        judge("DEREK", d["has_p"] and d["vwap"] is not None,
              "probability and planned price recorded")
        if al_runs is None:
            st["path"].append({"stage": "ALLOCATOR",
                               "result": "UNAVAILABLE",
                               "note": "MIGRATION_208_NOT_APPLIED"})
        else:
            judge("ALLOCATOR",
                  None if not any(t >= d["at"] for t in al_runs)
                  else d["decision_id"] in alloc,
                  "shadow allocation candidate after the decision")
        p = pos.get(d["decision_id"])
        ex_ok = p is not None or (archer_ids is not None
                                  and d["decision_id"] in archer_ids)
        if judge("EXECUTION", ex_ok, "entry fill (or Archer plan)") == \
                "PASS" and p is not None:
            if judge("XAVIER", p["group_id"] in reviewed,
                     "Xavier reviewed the position") == "PASS":
                if pm is None:
                    st["path"].append({"stage": "AUDREY",
                                       "result": "UNAVAILABLE",
                                       "note": "MIGRATION_209_NOT_APPLIED"})
                else:
                    judge("AUDREY", None if p["close_at"] is None
                          else p["group_id"] in pm,
                          "postmortem of the closed position")
        origins[st["origin"] or "NONE"] += 1
        if len(traces) < 200:
            traces.append(st)
    rows = [row("MACRO", s, "FAILURE_ORIGIN", stage_res[s] or None,
                why="NO_DECISION_REACHED_THIS_STAGE",
                detail={"failures_originating_here": origins.get(s, 0)})
            for s in STAGES]
    return {"rows": rows, "origins": dict(sorted(origins.items())),
            "traces": traces}


# ═════════════════════════════════════════════════════════════════════
# THE READ (bounded, SELECT only)
# ═════════════════════════════════════════════════════════════════════

async def load(conn, *, since: float, until: float, account_id,
               group_ids, limit: int = 5000) -> dict:
    db: dict = {}
    db["decisions"] = [dict(r) for r in await conn.fetch(
        "SELECT d.decision_id, extract(epoch FROM d.decided_at)::float8 AS at,"
        "       d.verdict, d.refusal, d.valuation_id, d.book_obs_id, "
        "       (coalesce(d.p_blended, d.p_pinnacle, d.p_internal) "
        "        IS NOT NULL) AS has_p, "
        "       coalesce(d.p_blended, d.p_pinnacle, d.p_internal) AS p, "
        "       d.limit_price, d.proposed_qty::float8 AS proposed_qty, "
        "       (d.economics->'acquisition'->>'vwap')::float8 AS vwap, "
        "       (v.id IS NOT NULL) AS val_ok, (b.obs_id IS NOT NULL) AS book_ok"
        "  FROM paper_decisions d "
        "  LEFT JOIN external_valuations v ON v.id = d.valuation_id "
        "  LEFT JOIN paper_book_observations b ON b.obs_id = d.book_obs_id "
        " WHERE d.decided_at >= to_timestamp($1) "
        "   AND d.decided_at <= to_timestamp($2) "
        "   AND ($3::text IS NULL OR d.account_id = $3) "
        " ORDER BY d.decided_at DESC LIMIT $4",
        float(since), float(until), account_id, int(limit))]
    db["intents"] = None
    if await R.regclass(conn, "execution_intents"):
        it = [dict(r) for r in await conn.fetch(
            "SELECT intent_id, coalesce(live_eligible, false) AS "
            "       live_eligible FROM execution_intents "
            " WHERE actual_mirror_id IS NOT NULL "
            "   AND decided_at >= to_timestamp($1) "
            "   AND decided_at <= to_timestamp($2) LIMIT $3",
            float(since), float(until), int(limit))]
        db["intents"] = it or None
    gids = sorted({g for g in group_ids if g})
    db["reviews"] = [dict(r) for r in await conn.fetch(
        "SELECT review_id, group_id, recommendation, "
        "       (measure IS NOT NULL) AS has_measure, "
        "       (selection IS NOT NULL) AS has_selection, "
        "       CASE WHEN jsonb_typeof(alternatives) = 'array' "
        "            THEN jsonb_array_length(alternatives) "
        "            WHEN jsonb_typeof(alternatives) = 'object' "
        "            THEN (SELECT count(*) FROM jsonb_object_keys("
        "                  alternatives))::int ELSE 0 END AS n_alternatives "
        "  FROM paper_xavier_reviews WHERE reviewed_at >= to_timestamp($1) "
        "   AND reviewed_at <= to_timestamp($2) "
        "   AND ($3::text IS NULL OR account_id = $3) "
        " ORDER BY reviewed_at DESC LIMIT $4",
        float(since), float(until), account_id, int(limit))]
    db["reviewed_groups"] = {r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_xavier_reviews "
        " WHERE group_id = ANY($1::text[])", gids)} if gids else set()
    db["handoff_groups"] = {r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_handoffs "
        " WHERE group_id = ANY($1::text[])", gids)} if gids else set()
    db["assessments"] = None
    if await R.regclass(conn, "xavier_management_assessments"):
        live = await R.regclass(conn, "smalllive_reviews")
        db["assessments"] = [dict(r) for r in await conn.fetch(
            "SELECT a.assessment_id, a.review_id, a.thesis_id, "
            "       a.evidence_state, a.recommendation, "
            "       a.reallocate->>'mode' AS reallocate_mode, "
            "       (r.review_id IS NOT NULL OR coalesce(s.ok, false)) "
            "       AS review_ok, (t.thesis_id IS NOT NULL) AS thesis_ok "
            "  FROM xavier_management_assessments a "
            "  LEFT JOIN paper_xavier_reviews r ON r.review_id = a.review_id "
            + ("  LEFT JOIN LATERAL (SELECT true AS ok FROM "
               "       smalllive_reviews sr WHERE sr.review_id = a.review_id "
               "       LIMIT 1) s ON true " if live else
               "  LEFT JOIN LATERAL (SELECT NULL::boolean AS ok) s ON true ")
            + "  LEFT JOIN xavier_entry_theses t ON t.thesis_id = a.thesis_id "
            " WHERE a.assessed_at >= to_timestamp($1) "
            "   AND a.assessed_at <= to_timestamp($2) "
            " ORDER BY a.assessed_at DESC LIMIT $3",
            float(since), float(until), int(limit))]
    db["karen"] = None
    if await R.regclass(conn, "karen_challenges"):
        db["karen"] = [dict(r) for r in await conn.fetch(
            "SELECT challenge_id, evidence_refs, claim, severity, state, "
            "       outcome, resolved_by, production_effect, blocked, "
            "       false_block FROM karen_challenges "
            " WHERE challenged_at >= to_timestamp($1) "
            "   AND challenged_at <= to_timestamp($2) "
            " ORDER BY challenged_at DESC LIMIT $3",
            float(since), float(until), int(limit))]
        want: dict = {}
        for c in db["karen"]:
            for ref in C.jload(c["evidence_refs"]) or []:
                if isinstance(ref, dict) and ref.get("kind") in RESOLVERS:
                    want.setdefault(ref["kind"], set()).add(str(ref.get(
                        "id")))
        res: dict = {}
        for kind, ids in sorted(want.items()):
            if not await R.regclass(conn, kind):
                res[kind] = set()
                continue
            key = RESOLVERS[kind]          # fixed whitelist, never data
            res[kind] = {str(r["k"]) for r in await conn.fetch(
                "SELECT %s::text AS k FROM %s WHERE %s::text = "
                "ANY($1::text[])" % (key, kind, key), sorted(ids))}
        db["resolved_refs"] = res
    db["findings"] = [dict(r) for r in await conn.fetch(
        "SELECT finding_id, kind, severity, subject, "
        "       (jsonb_typeof(detail) = 'object') AS detail_ok "
        "  FROM paper_audrey_findings WHERE found_at >= to_timestamp($1) "
        "   AND found_at <= to_timestamp($2) "
        " ORDER BY found_at DESC LIMIT $3",
        float(since), float(until), int(limit))]
    db["risk_checks"] = None
    if await R.regclass(conn, "intel_audrey_risk_checks"):
        db["risk_checks"] = [dict(r) for r in await conn.fetch(
            "SELECT c.run_id || '|' || c.book || '|' || c.metric AS key, "
            "       c.agrees, c.finding_id, (f.finding_id IS NOT NULL) "
            "       AS finding_ok FROM intel_audrey_risk_checks c "
            "  LEFT JOIN paper_audrey_findings f "
            "    ON f.finding_id = c.finding_id "
            " WHERE c.computed_at >= to_timestamp($1) "
            "   AND c.computed_at <= to_timestamp($2) "
            " ORDER BY c.computed_at DESC LIMIT $3",
            float(since), float(until), int(limit))]
    db["allocations"] = db["allocation_runs"] = None
    db["allocated_decisions"] = set()
    if await R.regclass(conn, "intel_allocations"):
        db["allocations"] = [dict(r) for r in await conn.fetch(
            "SELECT a.run_id || '|' || a.candidate_id AS key, a.run_id, "
            "       a.decision_id, a.group_id, a.shadow_usd, a.label, "
            "       extract(epoch FROM a.computed_at)::float8 AS at, "
            "       CASE WHEN jsonb_typeof(a.reasons) = 'array' "
            "            THEN jsonb_array_length(a.reasons) ELSE 0 END "
            "            AS n_reasons, "
            "       (d.decision_id IS NOT NULL) AS decision_ok, "
            "       EXISTS (SELECT 1 FROM paper_orders o "
            "                WHERE o.group_id = a.group_id) AS group_ok "
            "  FROM intel_allocations a "
            "  LEFT JOIN paper_decisions d ON d.decision_id = a.decision_id "
            " WHERE a.computed_at >= to_timestamp($1) "
            "   AND a.computed_at <= to_timestamp($2) "
            " ORDER BY a.computed_at DESC LIMIT $3",
            float(since), float(until), int(limit))]
        db["allocation_runs"] = sorted({a["at"] for a in db["allocations"]})
        db["allocated_decisions"] = {a["decision_id"] for a in
                                     db["allocations"] if a["decision_id"]}
    db["postmortem_groups"] = None
    if await R.regclass(conn, "position_postmortems"):
        db["postmortem_groups"] = {r["group_id"] for r in await conn.fetch(
            "SELECT DISTINCT group_id FROM position_postmortems "
            " WHERE group_id = ANY($1::text[])", gids)} if gids else set()
    return db


async def decisions_exist(conn, ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    got = {r["decision_id"] for r in await conn.fetch(
        "SELECT decision_id FROM paper_decisions "
        " WHERE decision_id = ANY($1::text[])", ids)}
    return {i: i in got for i in ids}


def compute(*, db: dict, positions: list, archer_rows, archer_why, scout_rows,
            scout_why, decision_ok: dict) -> dict:
    paper = [p for p in positions if p["book"] == "PAPER"]
    rows = (derek(db, paper) + xavier(db, positions) + karen(db)
            + audrey(db) + allocator_agent(db)
            + iface_agent("ARCHER", archer_rows, archer_why, decision_ok)
            + iface_agent("SCOUT", scout_rows, scout_why, decision_ok))
    rows += handoffs(db, paper, archer_rows, archer_why)
    m = macro(db, paper, archer_rows)
    rows += m["rows"]
    return {"rows": rows, "macro": {"origins": m["origins"],
                                    "traces": m["traces"]}}
