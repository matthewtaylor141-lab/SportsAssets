from __future__ import annotations

REQUIRED=("value","as_of","source","status")

def metric_truth_gate(metric:dict, *, now:float, max_age_s:float)->dict:
    blockers=[]
    for k in REQUIRED:
        if k not in metric: blockers.append("MISSING_"+k.upper())
    if "as_of" in metric and now-float(metric["as_of"])>max_age_s:
        blockers.append("METRIC_STALE")
    if metric.get("status")=="GREEN" and blockers:
        blockers.append("STALE_OR_INCOMPLETE_METRIC_CANNOT_BE_GREEN")
    if "numerator" in metric or "denominator" in metric:
        if metric.get("numerator") is None or metric.get("denominator") is None:
            blockers.append("DENOMINATOR_EVIDENCE_INCOMPLETE")
    return {"green":not blockers,"blockers":tuple(blockers)}
