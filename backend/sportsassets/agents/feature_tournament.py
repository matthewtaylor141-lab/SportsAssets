"""THE FEATURE TOURNAMENT'S EVALUATOR: BASELINE PINNAPI vs PINNAPI + FEATURE.

This is the calibration engine's verdict on one of Scout's frozen forward
tests -- NOT Scout's. It writes as CALIBRATION_ENGINE (a rule-based role,
not an agent identity) and the database refuses a verdict recorded by Scout
(migration 217: evaluated_by may not be EDDIE / SCOUT).

  * nothing is evaluated before the PREDECLARED minimum sample has settled
    outcomes -- the tournament stays UNDER_TEST with its progress;
  * the score is the PREDECLARED metric (Brier, or log loss) of the frozen
    predictions only: every sample was predicted at or after the freeze,
    from feature values observed before the prediction, before its outcome;
  * VALIDATED requires the predeclared improvement over the baseline;
    anything less is REJECTED (no incremental out-of-sample value);
  * the feature's state follows the verdict, set by the evaluator.

Pure scoring (`score`) is separate from the one write (`evaluate`).
"""
from __future__ import annotations

import json
import math
import time

EVALUATOR = "CALIBRATION_ENGINE"


def brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def log_loss(ps, ys) -> float:
    eps = 1e-12
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(
        max(1 - p, eps)) for p, y in zip(ps, ys)) / len(ps)


def score(samples: list, *, metric: str, min_sample: int,
          min_improvement: float) -> dict:
    """THE VERDICT FROM FROZEN, SETTLED SAMPLES. Pure.

    `samples`: [{p_baseline, p_challenger, outcome}] with outcome 0/1.
    Returns {ready, n, baseline_score, challenger_score, improvement,
    verdict, reason, log_loss_improvement}."""
    s = [x for x in samples if x.get("outcome") in (0, 1)]
    n = len(s)
    if n < int(min_sample):
        return {"ready": False, "n": n, "verdict": None,
                "reason": "SETTLED_SAMPLES_BELOW_PREDECLARED_MINIMUM "
                          "(%d < %d)" % (n, int(min_sample))}
    yb = [float(x["p_baseline"]) for x in s]
    yc = [float(x["p_challenger"]) for x in s]
    ys = [int(x["outcome"]) for x in s]
    fn = brier if metric == "BRIER" else log_loss
    b, c = fn(yb, ys), fn(yc, ys)
    imp = b - c                         # lower is better for both metrics
    ll = log_loss(yb, ys) - log_loss(yc, ys)
    ok = imp >= float(min_improvement)
    return {"ready": True, "n": n, "baseline_score": round(b, 8),
            "challenger_score": round(c, 8), "improvement": round(imp, 8),
            "log_loss_improvement": round(ll, 8),
            "verdict": "VALIDATED" if ok else "REJECTED",
            "reason": ("%s improved by %.6f >= the predeclared %.6f over %d "
                       "frozen forward samples" % (metric, imp,
                                                    float(min_improvement), n)
                       if ok else
                       "NO_INCREMENTAL_OUT_OF_SAMPLE_VALUE: %s improvement "
                       "%.6f < the predeclared %.6f over %d frozen forward "
                       "samples" % (metric, imp, float(min_improvement), n))}


async def evaluate(conn, tournament_id: str, *, now: float | None = None
                   ) -> dict:
    """Evaluate one frozen tournament and, when ready, record the verdict
    and move the feature (as CALIBRATION_ENGINE). Never raises."""
    at = float(now if now is not None else time.time())
    try:
        t = await conn.fetchrow(
            "SELECT * FROM scout_feature_tournaments WHERE tournament_id=$1",
            str(tournament_id))
        if t is None:
            return {"ok": False, "refusal": "NO_SUCH_TOURNAMENT"}
        if t["verdict"] is not None:
            return {"ok": True, "verdict": t["verdict"], "created": False}
        rows = [dict(r) for r in await conn.fetch(
            "SELECT p_baseline, p_challenger, outcome FROM "
            " scout_tournament_samples WHERE tournament_id=$1 AND outcome "
            " IS NOT NULL", t["tournament_id"])]
        got = score(rows, metric=t["metric"], min_sample=t["min_sample"],
                    min_improvement=t["min_improvement"])
        if not got["ready"]:
            return {"ok": True, "ready": False, "n": got["n"],
                    "reason": got["reason"]}
        result = {"log_loss_improvement": got["log_loss_improvement"],
                  "evaluator": EVALUATOR, "samples": got["n"],
                  "frozen_spec": {"metric": t["metric"],
                                  "min_sample": t["min_sample"],
                                  "min_improvement": t["min_improvement"]}}
        async with conn.transaction():
            await conn.execute(
                "UPDATE scout_feature_tournaments SET n=$2, baseline_score=$3,"
                " challenger_score=$4, improvement=$5, verdict=$6, "
                " verdict_reason=$7, evaluated_by=$8, "
                " evaluated_at=to_timestamp($9), result=$10::jsonb "
                " WHERE tournament_id=$1 AND verdict IS NULL",
                t["tournament_id"], got["n"], got["baseline_score"],
                got["challenger_score"], got["improvement"], got["verdict"],
                got["reason"], EVALUATOR, at, json.dumps(result))
            await conn.execute(
                "UPDATE scout_features SET state=$2, state_reason=$3, "
                " state_set_by=$4, state_set_at=to_timestamp($5), "
                " incremental_value=$6::jsonb WHERE feature_id=$1 "
                "   AND state='UNDER_TEST'", t["feature_id"], got["verdict"],
                got["reason"], EVALUATOR, at, json.dumps(dict(
                    result, improvement=got["improvement"],
                    baseline_score=got["baseline_score"],
                    challenger_score=got["challenger_score"])))
        return {"ok": True, "ready": True, "verdict": got["verdict"],
                "created": True, "n": got["n"],
                "improvement": got["improvement"]}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "TOURNAMENT_EVALUATION_FAILED",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
