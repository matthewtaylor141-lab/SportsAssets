"""KAREN'S CHALLENGES IN THE LEARNING LAYER (red team, no authority).
Pure; no I/O. The runner records what these return as rows whose actor is
KAREN (migration 218 CHECKs the actor per step); Karen proposes nothing,
evaluates nothing and approves nothing.

1. THE PROMOTION CHALLENGE of a candidate whose predeclared criteria were
   met. She tries to break the result rather than confirm it:
     TIME_STABILITY      the improvement must hold in BOTH halves of the
                         fixed sample (regime-overfitting / luck defence);
     SPORT_LEAKAGE       dropping the sport that contributes most must not
                         flip the sign (one-sport artefact defence);
     FEW_EVENTS          the mean with the top 5% most favourable pairs
                         removed must stay > 0 (outlier defence);
     REGIME              the largest regime with >= 30 pairs must not show
                         a non-positive mean (regime-overfitting defence);
     FORWARD_ONLY        the training window closed before registration and
                         every pair is after it (leakage / confirmation-
                         loop defence).
   Any failed check BLOCKS; the ladder stops there.

2. THE DESIGN CHALLENGE of an experiment before it may start: hypothesis
   directional and predeclared; primary metric with a direction; minimum
   sample at least the power calculation's; power >= 0.8; weights sum to 1;
   assignment unit is the fixture; stop after start; failure criteria for
   harm / no effect / underpowered / randomization; family size at least
   the declared plan; a seed not shared with another experiment.
"""
from __future__ import annotations

from . import common as C

VERSION = "POSLEARN_KAREN_V1"
MIN_REGIME_N = 30


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def promotion_challenge(pairs: list, *, document: dict,
                        registered_at: float) -> dict:
    """pairs: the FIXED sample, time-ordered: [{at, diff, sport, regime}]
    where diff > 0 favours the challenger."""
    checks = []
    n = len(pairs)

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    diffs = [p["diff"] for p in pairs]
    h = n // 2
    m1, m2 = _mean(diffs[:h]), _mean(diffs[h:])
    check("TIME_STABILITY", m1 is not None and m2 is not None
          and m1 > 0 and m2 > 0,
          {"first_half_mean": C.rnd(m1), "second_half_mean": C.rnd(m2)})
    by_sport: dict = {}
    for p in pairs:
        by_sport.setdefault(p.get("sport") or "UNKNOWN", []).append(
            p["diff"])
    if len(by_sport) >= 2:
        top = max(by_sport, key=lambda s: sum(by_sport[s]))
        rest = [p["diff"] for p in pairs
                if (p.get("sport") or "UNKNOWN") != top]
        check("SPORT_LEAKAGE", _mean(rest) is not None and _mean(rest) > 0,
              {"top_sport": top, "mean_without_top_sport":
               C.rnd(_mean(rest)), "sports": len(by_sport)})
    else:
        check("SPORT_LEAKAGE", True,
              {"single_sport": next(iter(by_sport), None),
               "note": "one sport in the sample: the claim is scoped to it"})
    k = max(1, int(round(0.05 * n)))
    trimmed = sorted(diffs)[:-k] if n > k else []
    check("FEW_EVENTS", _mean(trimmed) is not None and _mean(trimmed) > 0,
          {"removed_most_favourable": k,
           "trimmed_mean": C.rnd(_mean(trimmed))})
    by_reg: dict = {}
    for p in pairs:
        by_reg.setdefault(p.get("regime") or "UNKNOWN", []).append(p["diff"])
    big = [r for r in by_reg if len(by_reg[r]) >= MIN_REGIME_N]
    if big:
        worst = min(big, key=lambda r: _mean(by_reg[r]))
        check("REGIME", _mean(by_reg[worst]) > 0,
              {"worst_regime": worst, "mean": C.rnd(_mean(by_reg[worst])),
               "n": len(by_reg[worst])})
    else:
        check("REGIME", True, {"note": "no regime with %d pairs"
                                       % MIN_REGIME_N})
    win = document.get("training_window")
    end = win.get("end") if isinstance(win, dict) else None
    first = pairs[0]["at"] if pairs else None
    check("FORWARD_ONLY",
          (end is None or end <= registered_at)
          and (first is None or first >= registered_at),
          {"training_window_end": end, "registered_at": registered_at,
           "first_pair_at": first})
    blocked = [c["check"] for c in checks if not c["passed"]]
    return {"outcome": "BLOCKED" if blocked else "NOT_BLOCKED",
            "blocked_by": blocked, "checks": checks, "n": n,
            "version": VERSION}


def design_challenge(exp: dict, *, required_n_per_arm: int,
                     declared_family_size: int, other_seeds=()) -> dict:
    checks = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    hyp = str(exp.get("hypothesis") or "")
    check("HYPOTHESIS_DIRECTIONAL", len(hyp) >= 20 and any(
        w in hyp.lower() for w in ("increase", "decrease", "reduce",
                                   "improve")), {"hypothesis": hyp[:200]})
    pm = exp.get("primary_metric") or {}
    check("PRIMARY_METRIC_PREDECLARED", bool(pm.get("name"))
          and pm.get("direction") in ("INCREASE", "DECREASE"), pm)
    check("MIN_SAMPLE_MEETS_POWER",
          int(exp.get("min_sample") or 0) >= 2 * required_n_per_arm,
          {"min_sample": exp.get("min_sample"),
           "required_total": 2 * required_n_per_arm})
    check("POWER_TARGET", float(exp.get("power_target") or 0) >= 0.8,
          {"power_target": exp.get("power_target")})
    arms = exp.get("arms") or []
    check("WEIGHTS_SUM_TO_ONE",
          abs(sum(float(a.get("weight") or 0) for a in arms) - 1.0) < 1e-9
          and len(arms) >= 2, {"arms": arms})
    check("UNIT_IS_THE_FIXTURE",
          str(exp.get("assignment_unit") or "").upper().startswith(
              "FIXTURE"), {"unit": exp.get("assignment_unit")})
    check("STOP_AFTER_START",
          float(exp.get("stop_at") or 0) > float(exp.get("start_at") or 0))
    fc = exp.get("failure_criteria") or {}
    check("FAILURE_CRITERIA", all(k in fc for k in (
        "harm", "no_effect", "underpowered", "randomization")),
        sorted(fc))
    check("FAMILY_SIZE_AT_LEAST_THE_PLAN",
          int(exp.get("family_size") or 0) >= declared_family_size,
          {"family_size": exp.get("family_size"),
           "declared": declared_family_size})
    check("SEED_NOT_SHARED", exp.get("seed") not in set(other_seeds))
    check("NO_EARLY_STOP_FOR_EFFICACY",
          (exp.get("stopping_rule") or {}).get("early_stop_for_efficacy")
          is False)
    blocked = [c["check"] for c in checks if not c["passed"]]
    return {"outcome": "BLOCKED" if blocked else "NOT_BLOCKED",
            "blocked_by": blocked, "checks": checks, "version": VERSION}
