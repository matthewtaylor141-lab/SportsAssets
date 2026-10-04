"""5 · THE CAUSAL EXPERIMENT PLATFORM: prospective, randomized PAPER/SHADOW
experiments. Pure; no I/O.

PREDECLARED, IN FULL, BEFORE START: hypothesis, primary and secondary
metrics (name + direction), assignment unit, randomization (method, seed,
arms, weights), policy versions per arm (registration ids), start, stop,
minimum sample (from a stated power calculation), power target, alpha,
family and family size, stopping rule and failure criteria. Migration 218
refuses any change to these once the experiment has started, refuses a
start without a Karen design-challenge row that did not block, and checks
every assignment against the seeded draw.

RANDOMIZATION. draw(unit) = first 52 bits of SHA-256(seed ':' experiment ':'
unit) / 2^52 -- deterministic, recorded per unit, recomputed by the database
(poslearn_draw) and by Audrey's randomization audit. The draw depends on
nothing but the seed and the unit id: no feature, price or outcome can
steer an assignment (counterfactual-contamination defence).

ASSIGNMENT IS PERSISTED BEFORE THE OUTCOME, never changed, never deleted.
The analysis is intention-to-treat over EVERY assigned unit: a unit whose
arm passed contributes 0, a VOID contributes 0, an unresolved unit is
counted as missing and disclosed -- nothing is excluded after the fact.

SCOPE: the arms are two SHADOW policies evaluated on the same opportunity;
assignment decides which arm's decision is booked for that unit in the
experiment's own ledger of outcomes. Nothing reaches an order, a paper
order, a threshold or a probability.
"""
from __future__ import annotations

import hashlib
import math

from . import common as C
from . import models as M

VERSION = "POSLEARN_EXPERIMENTS_V1"
FAMILY = "EXPERIMENTS_V1"
DESIGN_REVIEW_S = 60.0
DURATION_DAYS = 120.0
ALPHA = 0.05
POWER = 0.80
ASSUMED_SD = 0.27
MDE = 0.02
AUDIT_EVERY = 50
DRAW_DENOM = float(16 ** 13)

#: THE DECLARED PLAN: every experiment of the family, fixed in code, so the
#: family size is the plan's and not the number that looked interesting.
PLAN = (
    {"experiment_id": "EXP_ENTRY_THRESHOLD_STEP_V1",
     "hypothesis": (
         "Raising the entry threshold by 0.5 pp (the DEREK_CHALLENGER_A "
         "rule) increases realized net edge per randomized fixture versus "
         "the approved threshold (the DEREK_V1 rule), in SHADOW."),
     "control": "DEREK_V1", "treatment": "DEREK_CHALLENGER_A",
     "seed": "poslearn-exp-threshold-step-v1-7f3a"},
    {"experiment_id": "EXP_AVOIDANCE_FILTER_V1",
     "hypothesis": (
         "Refusing the approved rule's entries that the AVOIDANCE model "
         "marks AVOID (the DEREK_CHALLENGER_B rule) increases realized net "
         "edge per randomized fixture versus the approved rule (DEREK_V1), "
         "in SHADOW."),
     "control": "DEREK_V1", "treatment": "DEREK_CHALLENGER_B",
     "seed": "poslearn-exp-avoidance-filter-v1-c41e"},
)
FAMILY_SIZE = len(PLAN)


def draw(seed: str, experiment_id: str, unit_id: str) -> float:
    h = hashlib.sha256(("%s:%s:%s" % (seed, experiment_id, unit_id))
                       .encode("utf-8")).hexdigest()
    return int(h[:13], 16) / DRAW_DENOM


def arm_for(d: float, arms: list) -> str:
    cum = 0.0
    for a in arms:
        cum += float(a["weight"])
        if d < cum:
            return a["arm"]
    return arms[-1]["arm"]


def power_n_per_arm(*, sd=ASSUMED_SD, mde=MDE, alpha=ALPHA, power=POWER,
                    family_size=FAMILY_SIZE) -> int:
    """Two-sample difference in means, two-sided, alpha Bonferroni-adjusted
    over the family: n = 2 (z_a + z_b)^2 sd^2 / mde^2 per arm."""
    za = C.adjusted_z(alpha, family_size)
    zb = C.norm_ppf(power)
    return int(math.ceil(2.0 * (za + zb) ** 2 * sd * sd / (mde * mde)))


def design(spec: dict, *, policy_versions: dict, now: float) -> dict:
    n_arm = power_n_per_arm()
    arms = [{"arm": "CONTROL", "weight": 0.5, "policy": spec["control"]},
            {"arm": "TREATMENT", "weight": 0.5, "policy": spec["treatment"]}]
    d = {
        "experiment_id": spec["experiment_id"],
        "hypothesis": spec["hypothesis"],
        "primary_metric": {
            "name": "realized_net_edge_per_unit", "direction": "INCREASE",
            "unit": "USD per contract per assigned fixture; 0 when the "
                    "arm's rule does not enter or the outcome is VOID"},
        "secondary_metrics": [
            {"name": "entry_rate", "direction": "REPORT"},
            {"name": "realized_net_edge_per_entry", "direction": "INCREASE"},
            {"name": "loss_rate_of_entries", "direction": "DECREASE"}],
        "assignment_unit": ("FIXTURE: the opportunity unit (the valuation's "
                            "event_key), one assignment per fixture"),
        "randomization": {
            "method": "SHA256_SEEDED_DRAW",
            "draw": "int(sha256(seed:experiment_id:unit)[:13], 16) / 16^13",
            "arms": arms, "weights_sum": 1.0,
            "verified_by": ["poslearn_draw() in the assignment trigger",
                            "Audrey's randomization audit"]},
        "seed": spec["seed"],
        "arms": arms,
        "policy_versions": policy_versions,
        "start_at": now + DESIGN_REVIEW_S,
        "stop_at": now + DESIGN_REVIEW_S + DURATION_DAYS * 86400.0,
        "min_sample": 2 * n_arm,
        "power_target": POWER, "alpha": ALPHA,
        "family": FAMILY, "family_size": FAMILY_SIZE,
        "stopping_rule": {
            "stop_when": "min_sample assigned units have a recorded outcome, "
                         "or stop_at -- whichever first",
            "early_stop_for_efficacy": False,
            "analyses": 1,
            "power_calculation": {
                "assumed_sd_per_unit": ASSUMED_SD, "mde": MDE,
                "alpha_adjusted": C.adjusted_alpha(ALPHA, FAMILY_SIZE),
                "power": POWER, "n_per_arm": n_arm}},
        "failure_criteria": {
            "harm": "treatment - control upper bound (adjusted) < 0 -> "
                    "HYPOTHESIS_REJECTED_HARM",
            "no_effect": "interval contains 0 at min_sample -> "
                         "NOT_SUPPORTED",
            "underpowered": "min_sample not reached by stop_at -> "
                            "INCONCLUSIVE_UNDERPOWERED",
            "randomization": "any Audrey randomization audit FAIL -> "
                             "INVALID_RANDOMIZATION"},
    }
    d["design_sha256"] = C.sha256_text(C.canonical(
        {k: v for k, v in d.items() if k != "design_sha256"}))
    return d


def unit_metric(arm_action: str, opp: dict, outcome: dict) -> dict:
    """The primary metric of one assigned unit under its arm's decision."""
    c = None
    if opp.get("price") is not None and opp.get("fee") is not None:
        c = float(opp["price"]) + float(opp["fee"])
    entered = arm_action == "ENTER"
    if outcome.get("outcome_class") != "RESOLVED" or not entered:
        val = 0.0
    else:
        val = int(outcome["o"]) - c
    return {"arm_action": arm_action, "entered": entered,
            "outcome_class": outcome.get("outcome_class"),
            "o": outcome.get("o"), "all_in_cost": c,
            "realized_net_edge_per_unit": C.rnd(val, 10)}


def analyze(exp: dict, assignments: list, outcomes: dict) -> dict:
    """Intention-to-treat over EVERY assigned unit."""
    z = C.adjusted_z(float(exp["alpha"]), int(exp["family_size"]))
    arms = {}
    missing = {}
    for a in assignments:
        o = outcomes.get(a["unit_id"])
        if o is None:
            missing[a["arm"]] = missing.get(a["arm"], 0) + 1
            continue
        arms.setdefault(a["arm"], []).append(o)
    out = C.Out(assigned=len(assignments), z_adjusted=round(z, 6),
                missing_outcomes=missing, analysis="INTENTION_TO_TREAT")
    stats = {}
    for arm, rows in arms.items():
        xs = [float(r.get("realized_net_edge_per_unit") or 0.0) for r in rows]
        m, lo, hi, n = C.mean_ci(xs, 1.959964)
        ent = [r for r in rows if r.get("entered")]
        stats[arm] = {"n": n, "mean": C.rnd(m), "ci95": None if lo is None
                      else [C.rnd(lo), C.rnd(hi)],
                      "entry_rate": C.rnd(len(ent) / n) if n else None,
                      "loss_rate_of_entries": C.rnd(
                          sum(1 for r in ent if r.get("o") == 0) / len(ent))
                      if ent else None}
    out["arms"] = stats
    c, t = arms.get("CONTROL") or [], arms.get("TREATMENT") or []
    if len(c) < 2 or len(t) < 2:
        out.put("difference", None, "FEWER_THAN_TWO_OUTCOMES_IN_AN_ARM")
        out["verdict"] = "INCONCLUSIVE"
        return out
    xc = [float(r.get("realized_net_edge_per_unit") or 0.0) for r in c]
    xt = [float(r.get("realized_net_edge_per_unit") or 0.0) for r in t]
    mc, mt = sum(xc) / len(xc), sum(xt) / len(xt)
    vc = sum((x - mc) ** 2 for x in xc) / (len(xc) - 1)
    vt = sum((x - mt) ** 2 for x in xt) / (len(xt) - 1)
    se = math.sqrt(vc / len(xc) + vt / len(xt))
    diff = mt - mc
    out.put("difference", C.rnd(diff))
    out["difference_ci_adjusted"] = [C.rnd(diff - z * se),
                                     C.rnd(diff + z * se)]
    n_out = len(xc) + len(xt)
    if n_out < int(exp["min_sample"]):
        out["verdict"] = "INCONCLUSIVE_UNDERPOWERED"
    elif diff - z * se > 0:
        out["verdict"] = "SUPPORTED"
    elif diff + z * se < 0:
        out["verdict"] = "HYPOTHESIS_REJECTED_HARM"
    else:
        out["verdict"] = "NOT_SUPPORTED"
    return out


def threshold_of(policy_doc: dict) -> float:
    return float((policy_doc.get("parameters") or {}).get(
        "threshold_pp", M.DEFAULT_THRESHOLD_PP))
