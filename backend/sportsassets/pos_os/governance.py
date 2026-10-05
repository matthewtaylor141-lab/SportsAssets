"""AUTONOMOUS EXPERIMENT GOVERNANCE AUDIT (RESEARCH). Pure; no I/O.

The DESIGN exists (sportsassets/poslearn/experiments.py `design`: hypothesis,
metrics, power, stopping rule and failure criteria predeclared before start,
one analysis, no early stop for efficacy; migration 218 refuses a change
after start). This module AUDITS THE RECORD against that contract, and the
improvement pipeline's and paper policy parameters' promotions against the
same rules, over recorded rows only:

  EXPERIMENT  REGISTERED_AFTER_START          predeclaration after start
              NO_PRIMARY_METRIC / NO_STOPPING_RULE / NO_MIN_SAMPLE
              EARLY_EFFICACY_STOP_ALLOWED     the rule lets a look stop it
              MULTIPLE_LOOKS_WITHOUT_SPENDING analyses > 1, no alpha spending
              CONCLUSIVE_BEFORE_STOPPING_RULE a SUPPORTED / HARM verdict
                                              recorded before stop_at with
                                              fewer outcomes than min_sample
                                              (a peek promoted to a verdict)
  CANDIDATE   PROMOTED_WITHOUT_PREDECLARATION approved / released with no
                                              hypothesis or success metrics
              APPROVED_BEFORE_EVALUATION
  TRIAL       SCORED_BEFORE_WINDOW_CLOSED     a trial recorded before its own
                                              evaluation boundary (peeking)
  RELEASE     RELEASED_WITHOUT_CANDIDATE / RELEASED_BEFORE_EVALUATION /
              RELEASED_BEFORE_APPROVAL
  ACTIVATION  ACTIVATED_WITHOUT_EVALUATION    an ACTIVATE with no evaluation

The audit governs nothing by itself: it names violations on a page.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_GOVERNANCE_V1"
PROMOTED_STATES = ("APPROVED", "CANARY", "RELEASED", "ROLLED_BACK")
CONCLUSIVE = ("SUPPORTED", "HYPOTHESIS_REJECTED_HARM")


def _v(kind, subject, code, **detail):
    return {"kind": kind, "subject": subject, "violation": code,
            "detail": detail}


def audit_experiment(e):
    out = []
    sid = e.get("experiment_id")
    reg, start = C.num(e.get("registered_at")), C.num(e.get("start_at"))
    if reg is not None and start is not None and reg > start:
        out.append(_v("EXPERIMENT", sid, "REGISTERED_AFTER_START",
                      registered_at=reg, start_at=start))
    pm = e.get("primary_metric") or {}
    if not (isinstance(pm, dict) and pm.get("name") and pm.get("direction")):
        out.append(_v("EXPERIMENT", sid, "NO_PRIMARY_METRIC"))
    sr = e.get("stopping_rule")
    if not isinstance(sr, dict) or not sr:
        out.append(_v("EXPERIMENT", sid, "NO_STOPPING_RULE"))
        sr = {}
    if not (C.num(e.get("min_sample")) or 0) > 0:
        out.append(_v("EXPERIMENT", sid, "NO_MIN_SAMPLE"))
    if sr.get("early_stop_for_efficacy") is True:
        out.append(_v("EXPERIMENT", sid, "EARLY_EFFICACY_STOP_ALLOWED"))
    if (C.num(sr.get("analyses")) or 1) > 1 and not sr.get("alpha_spending"):
        out.append(_v("EXPERIMENT", sid, "MULTIPLE_LOOKS_WITHOUT_SPENDING",
                      analyses=sr.get("analyses")))
    res = e.get("result") or {}
    if isinstance(res, dict) and res.get("verdict") in CONCLUSIVE:
        n = sum(int((a or {}).get("n") or 0)
                for a in (res.get("arms") or {}).values())
        at, stop = C.num(e.get("status_changed_at")), C.num(e.get("stop_at"))
        if at is not None and stop is not None and at < stop and \
                n < int(C.num(e.get("min_sample")) or 0):
            out.append(_v("EXPERIMENT", sid, "CONCLUSIVE_BEFORE_STOPPING_RULE",
                          outcomes=n, min_sample=e.get("min_sample"),
                          verdict=res.get("verdict")))
    return out


def audit_improvements(rows):
    out = []
    cands = {r["candidate_id"]: r for r in rows if r.get("kind") ==
             "CANDIDATE"}
    for c in cands.values():
        if c.get("state") in PROMOTED_STATES:
            if not (c.get("hypothesis") or "").strip() or not c.get(
                    "success_metrics"):
                out.append(_v("CANDIDATE", c["candidate_id"],
                              "PROMOTED_WITHOUT_PREDECLARATION",
                              state=c.get("state")))
            ev, ap = C.num(c.get("evaluated_at")), C.num(c.get("approved_at"))
            if ap is not None and (ev is None or ap < ev):
                out.append(_v("CANDIDATE", c["candidate_id"],
                              "APPROVED_BEFORE_EVALUATION",
                              evaluated_at=ev, approved_at=ap))
    for t in (r for r in rows if r.get("kind") == "TRIAL"):
        b, at = C.num(t.get("evaluation_boundary_at")), C.num(
            t.get("created_at"))
        if b is not None and at is not None and at < b:
            out.append(_v("TRIAL", t.get("trial_id"),
                          "SCORED_BEFORE_WINDOW_CLOSED",
                          created_at=at, evaluation_boundary_at=b))
    for r in (r for r in rows if r.get("kind") == "RELEASE"):
        c = cands.get(r.get("candidate_id"))
        rel = C.num(r.get("released_at"))
        if c is None:
            out.append(_v("RELEASE", r.get("release_id"),
                          "RELEASED_WITHOUT_CANDIDATE"))
            continue
        ev, ap = C.num(c.get("evaluated_at")), C.num(c.get("approved_at"))
        if ev is None or (rel is not None and ev > rel):
            out.append(_v("RELEASE", r.get("release_id"),
                          "RELEASED_BEFORE_EVALUATION",
                          evaluated_at=ev, released_at=rel))
        if ap is None or (rel is not None and ap > rel):
            out.append(_v("RELEASE", r.get("release_id"),
                          "RELEASED_BEFORE_APPROVAL",
                          approved_at=ap, released_at=rel))
    return out


def audit_activations(rows):
    return [_v("ACTIVATION", a.get("activation_id"),
               "ACTIVATED_WITHOUT_EVALUATION", policy_key=a.get("policy_key"))
            for a in rows if a.get("kind") == "ACTIVATE"
            and not a.get("evaluation_id")]


def build(inputs, *, now):
    names = ["experiments", "improvements", "activations"]
    miss = C.missing(inputs, names)
    if len(miss) == len(names):
        return C.unread(miss, inputs, audit=C.BUILT)
    viol = []
    counts = {}
    if inputs.get("experiments") is not None:
        counts["experiments"] = len(inputs["experiments"])
        for e in inputs["experiments"]:
            viol += audit_experiment(e)
    if inputs.get("improvements") is not None:
        for k in ("CANDIDATE", "TRIAL", "RELEASE"):
            counts[k.lower() + "s"] = sum(
                1 for r in inputs["improvements"] if r.get("kind") == k)
        viol += audit_improvements(inputs["improvements"])
    if inputs.get("activations") is not None:
        counts["activations"] = len(inputs["activations"])
        viol += audit_activations(inputs["activations"])
    audited = sum(counts.values())
    by_code: dict = {}
    for v in viol:
        by_code[v["violation"]] = by_code.get(v["violation"], 0) + 1
    data = {"version": VERSION, "audited": counts,
            "verdict": ("NOTHING_RECORDED" if not audited else
                        "VIOLATIONS" if viol else "COMPLIANT"),
            "violations_by_code": by_code, "violations": viol[:50],
            "unread_inputs": miss,
            "design": "sportsassets/poslearn/experiments.py:design"}
    return C.section(C.OK if audited else C.EMPTY,
                     None if audited else "%s: no experiment, candidate, "
                     "trial, release or activation recorded" % C.R_NO_ROWS,
                     audit=C.BUILT, data=data,
                     sources=["poslearn_experiments", "improvement_candidates",
                              "improvement_trials", "improvement_releases",
                              "paper_policy_parameter_activations"])
