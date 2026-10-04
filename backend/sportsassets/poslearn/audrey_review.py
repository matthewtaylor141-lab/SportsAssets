"""AUDREY'S INDEPENDENT EVALUATIONS IN THE LEARNING LAYER. Recorded as rows
whose actor is AUDREY (migration 218 CHECKs it).

INDEPENDENT BY CONSTRUCTION. This module does NOT import the runner's
scoring, models, agents or experiments modules, nor intel/calibration
(pinned by tests/test_poslearn_authority.py): it reads the raw forecast and
outcome rows itself (READ-ONLY SELECTs below) and recomputes with its own
formulas. A disagreement with the runner's recorded numbers, a criterion
not met on her recompute, or a result she cannot recompute independently is
a FAIL (fail-closed).

1. PROMOTION EVALUATION of a candidate Karen did not block:
     MODEL          per-pair log loss and Brier from the stored
                    probabilities and outcomes, on the same FIRST
                    minimum_sample pairs; mean, Bonferroni-adjusted interval;
                    the declared criteria re-applied.
     AGENT_VARIANT  DEREK and ALLOCATOR variants: per-opportunity P&L from
                    the stored action / shadow size, price, fee and outcome.
                    XAVIER and EDDIE variants need a path replay or fills she
                    does not recompute independently here -> FAIL with that
                    reason (not a pass by default).
2. RANDOMIZATION AUDIT of an experiment: every assignment's draw and arm
   recomputed from the seed; arm counts against the weights (normal
   approximation of the binomial, |z| < 3.29 i.e. p > 0.001); balance of the
   pre-assignment PinnAPI probability across arms once each arm has >= 50
   units: the standardized difference must be inside 3.29 x its sampling
   standard error sqrt(1/n0 + 1/n1) (p > 0.001) -- a fixed 0.2 rule would
   fail a correct randomization by chance at these sample sizes.
"""
from __future__ import annotations

import hashlib
import math

VERSION = "POSLEARN_AUDREY_V1"
EPS = 1e-6
TOLERANCE = 1e-6


def _ll(p, o):
    q = min(max(float(p), EPS), 1.0 - EPS)
    return -math.log(q) if int(o) == 1 else -math.log(1.0 - q)


def _ppf(q):
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if 0.5 * (1.0 + math.erf(mid / math.sqrt(2.0))) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _z(alpha, family_size):
    return _ppf(1.0 - (float(alpha) / max(1, int(family_size))) / 2.0)


def _mci(xs, z):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, m - z * sd / math.sqrt(n), m + z * sd / math.sqrt(n)


MODEL_PAIRS_SQL = """
    SELECT o.opportunity_id,
           extract(epoch FROM o.opportunity_at)::float8 AS at,
           cf.probability AS p_champion, xf.probability AS p_challenger,
           r.outcome
      FROM poslearn_forecasts xf
      JOIN poslearn_opportunities o ON o.opportunity_id = xf.opportunity_id
      JOIN poslearn_outcomes r ON r.opportunity_id = xf.opportunity_id
      JOIN poslearn_forecasts cf ON cf.opportunity_id = xf.opportunity_id
      JOIN poslearn_registrations cr
        ON cr.registration_id = cf.registration_id
     WHERE xf.registration_id = $1 AND cr.subject_id = $2
       AND r.outcome_class = 'RESOLVED'
       AND xf.probability IS NOT NULL AND cf.probability IS NOT NULL
       AND o.opportunity_at >= $3::timestamptz
     ORDER BY o.opportunity_at, o.opportunity_id
     LIMIT $4
"""

AGENT_PAIRS_SQL = """
    SELECT o.opportunity_id,
           extract(epoch FROM o.opportunity_at)::float8 AS at,
           o.price, o.fee, r.outcome, r.outcome_class,
           xf.action AS x_action, xf.shadow_usd AS x_usd,
           bf.action AS b_action, bf.shadow_usd AS b_usd
      FROM poslearn_forecasts xf
      JOIN poslearn_opportunities o ON o.opportunity_id = xf.opportunity_id
      JOIN poslearn_outcomes r ON r.opportunity_id = xf.opportunity_id
      JOIN poslearn_forecasts bf ON bf.opportunity_id = xf.opportunity_id
      JOIN poslearn_registrations br
        ON br.registration_id = bf.registration_id
     WHERE xf.registration_id = $1 AND br.subject_id = $2
       AND o.opportunity_at >= $3::timestamptz
     ORDER BY o.opportunity_at, o.opportunity_id
     LIMIT $4
"""


def _agent_pnl(action, usd, price, fee, outcome, outcome_class, agent):
    if action in ("PASS", "ABSTAIN") or price is None or fee is None:
        return 0.0
    c = float(price) + float(fee)
    o = int(outcome) if outcome_class == "RESOLVED" else None
    if o is None:
        return 0.0
    qty = 1.0 if agent == "DEREK" else float(usd or 0.0) / c
    return qty * (o - c)


async def evaluate_candidate(conn, *, registration: dict, recorded: dict,
                             base_subject: str) -> dict:
    """registration: {registration_id, kind, document, registered_at};
    recorded: the runner's verdict numbers for the same fixed sample."""
    import datetime as _dt

    doc = registration["document"]
    n_min = int(doc["minimum_sample"])
    z = _z(float(doc["promotion_threshold"].get("alpha", 0.05)),
           int(doc["family_size"]))
    since = _dt.datetime.fromtimestamp(float(registration["registered_at"]),
                                       _dt.timezone.utc)
    findings = {"version": VERSION, "n_required": n_min,
                "z_adjusted": round(z, 6)}
    if registration["kind"] == "MODEL":
        rows = await conn.fetch(MODEL_PAIRS_SQL,
                                registration["registration_id"],
                                base_subject, since, n_min)
        if len(rows) < n_min:
            findings["why"] = "FEWER_THAN_THE_FIXED_SAMPLE_ON_RECOMPUTE"
            return {"outcome": "FAIL", "findings": findings}
        ll = [_ll(r["p_champion"], r["outcome"])
              - _ll(r["p_challenger"], r["outcome"]) for r in rows]
        bs = [(r["p_champion"] - r["outcome"]) ** 2
              - (r["p_challenger"] - r["outcome"]) ** 2 for r in rows]
        m, lo, hi = _mci(ll, z)
        mb = sum(bs) / len(bs)
        ids = hashlib.sha256(",".join(r["opportunity_id"] for r in rows)
                             .encode()).hexdigest()
        findings.update(log_loss_improvement=round(m, 8),
                        ci_adjusted=[round(lo, 8), round(hi, 8)],
                        brier_improvement=round(mb, 8),
                        sample_ids_sha256=ids)
        agree = (abs(m - float(recorded.get("log_loss_improvement")
                               or float("nan"))) < TOLERANCE
                 and ids == recorded.get("sample_ids_sha256"))
        meets = lo > 0 and mb >= 0
    else:
        agent = registration["document"].get("agent")
        if agent not in ("DEREK", "ALLOCATOR"):
            findings["why"] = ("%s_VARIANTS_ARE_NOT_INDEPENDENTLY_"
                               "RECOMPUTABLE_HERE_FAIL_CLOSED" % agent)
            return {"outcome": "FAIL", "findings": findings}
        rows = await conn.fetch(AGENT_PAIRS_SQL,
                                registration["registration_id"],
                                base_subject, since, n_min)
        rows = [r for r in rows if r["outcome_class"] in ("RESOLVED",
                                                           "VOID")]
        if len(rows) < n_min:
            findings["why"] = "FEWER_THAN_THE_FIXED_SAMPLE_ON_RECOMPUTE"
            return {"outcome": "FAIL", "findings": findings}
        d = [_agent_pnl(r["x_action"], r["x_usd"], r["price"], r["fee"],
                        r["outcome"], r["outcome_class"], agent)
             - _agent_pnl(r["b_action"], r["b_usd"], r["price"], r["fee"],
                          r["outcome"], r["outcome_class"], agent)
             for r in rows]
        m, lo, hi = _mci(d, z)
        findings.update(mean_difference=round(m, 8),
                        ci_adjusted=[round(lo, 8), round(hi, 8)])
        agree = abs(m - float(recorded.get("mean_difference")
                              or float("nan"))) < TOLERANCE
        meets = lo > 0
    findings["agrees_with_runner"] = bool(agree)
    findings["criteria_met_on_recompute"] = bool(meets)
    ok = agree and meets
    if not ok:
        findings["why"] = ("RECOMPUTE_DISAGREES_WITH_THE_RUNNER" if not agree
                           else "CRITERIA_NOT_MET_ON_RECOMPUTE")
    return {"outcome": "PASS" if ok else "FAIL", "findings": findings}


def randomization_audit(exp: dict, assignments: list,
                        covariate: dict) -> dict:
    """assignments: [{unit_id, arm, draw}]; covariate: {unit_id: p}."""
    bad = []
    for a in assignments:
        h = hashlib.sha256(("%s:%s:%s" % (exp["seed"], exp["experiment_id"],
                                          a["unit_id"])).encode()).hexdigest()
        d = int(h[:13], 16) / float(16 ** 13)
        cum, arm = 0.0, None
        for x in exp["arms"]:
            cum += float(x["weight"])
            if arm is None and d < cum:
                arm = x["arm"]
        if abs(d - float(a["draw"])) > 1e-12 or arm != a["arm"]:
            bad.append(a["unit_id"])
    n = len(assignments)
    counts = {}
    for a in assignments:
        counts[a["arm"]] = counts.get(a["arm"], 0) + 1
    zs = {}
    for x in exp["arms"]:
        w = float(x["weight"])
        k = counts.get(x["arm"], 0)
        zs[x["arm"]] = (None if n == 0 else
                        (k - n * w) / math.sqrt(n * w * (1 - w))
                        if 0 < w < 1 else 0.0)
    balance = {}
    big = all(counts.get(x["arm"], 0) >= 50 for x in exp["arms"])
    std_diff = None
    std_limit = None
    if big and len(exp["arms"]) == 2:
        a0, a1 = exp["arms"][0]["arm"], exp["arms"][1]["arm"]
        g0 = [covariate[a["unit_id"]] for a in assignments
              if a["arm"] == a0 and covariate.get(a["unit_id"]) is not None]
        g1 = [covariate[a["unit_id"]] for a in assignments
              if a["arm"] == a1 and covariate.get(a["unit_id"]) is not None]
        if len(g0) > 1 and len(g1) > 1:
            m0, m1 = sum(g0) / len(g0), sum(g1) / len(g1)
            v0 = sum((x - m0) ** 2 for x in g0) / (len(g0) - 1)
            v1 = sum((x - m1) ** 2 for x in g1) / (len(g1) - 1)
            pooled = math.sqrt((v0 + v1) / 2.0) or 1e-9
            std_diff = (m1 - m0) / pooled
            std_limit = 3.29 * math.sqrt(1.0 / len(g0) + 1.0 / len(g1))
    balance["p_reference_standardized_difference"] = (
        None if std_diff is None else round(std_diff, 6))
    balance["limit"] = None if std_limit is None else round(std_limit, 6)
    imbalance = any(z is not None and abs(z) > 3.29 for z in zs.values())
    covariate_bad = std_diff is not None and abs(std_diff) > std_limit
    ok = not bad and not imbalance and not covariate_bad
    return {"outcome": "PASS" if ok else "FAIL",
            "findings": {"version": VERSION, "assignments": n,
                         "draw_or_arm_mismatches": bad[:50],
                         "arm_counts": counts,
                         "arm_count_z": {k: (None if v is None
                                             else round(v, 6))
                                         for k, v in zs.items()},
                         "balance": balance,
                         "balance_checked": big}}
