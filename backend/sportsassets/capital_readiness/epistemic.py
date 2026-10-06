"""Epistemic uncertainty shrinkage for PAPER/SHADOW decisions.

This module never grants authority. It converts evidence quality into a factor
in [0,1] that may only shrink risk-adjusted economic value or proposed size.
"""
from __future__ import annotations

import math
from . import common as C

VERSION = "EPISTEMIC_CONFIDENCE_V1"
MIN_CAL_N = 30
TARGET_CAL_N = 300
MIN_EXEC_N = 10
TARGET_EXEC_N = 100
MAX_BRIER = 0.25
IDEAL_SLOPE = 1.0
MAX_SLOPE_DEVIATION = 0.75


def _evidence_factor(n, min_n, target_n):
    n = max(0.0, float(n or 0))
    if n < min_n:
        return 0.0
    return C.clamp((n - min_n) / max(1.0, target_n - min_n))


def confidence(*, calibration_n=0, execution_n=0, brier=None,
               calibration_slope=None, regime_novelty=1.0,
               ood_score=1.0, data_quality=1.0):
    cal_n = _evidence_factor(calibration_n, MIN_CAL_N, TARGET_CAL_N)
    exe_n = _evidence_factor(execution_n, MIN_EXEC_N, TARGET_EXEC_N)

    if brier is None:
        brier_factor = 0.0
    else:
        brier_factor = C.clamp(1.0 - max(0.0, float(brier)) / MAX_BRIER)

    if calibration_slope is None:
        slope_factor = 0.0
    else:
        dev = abs(float(calibration_slope) - IDEAL_SLOPE)
        slope_factor = C.clamp(1.0 - dev / MAX_SLOPE_DEVIATION)

    novelty_factor = C.clamp(regime_novelty)
    ood_factor = C.clamp(ood_score)
    dq_factor = C.clamp(data_quality)

    # Geometric mean: one weak dimension materially reduces confidence while
    # no dimension can compensate for another by exceeding 1.
    pieces = [cal_n, exe_n, brier_factor, slope_factor,
              novelty_factor, ood_factor, dq_factor]
    if any(p <= 0 for p in pieces):
        factor = 0.0
    else:
        factor = math.prod(pieces) ** (1.0 / len(pieces))

    status = "MEASURED" if factor > 0 else "INSUFFICIENT_EVIDENCE"
    return {
        "version": VERSION,
        "status": status,
        "confidence_factor": C.rnd(factor),
        "components": {
            "calibration_sample": C.rnd(cal_n),
            "execution_sample": C.rnd(exe_n),
            "brier": C.rnd(brier_factor),
            "calibration_slope": C.rnd(slope_factor),
            "regime_novelty": C.rnd(novelty_factor),
            "out_of_distribution": C.rnd(ood_factor),
            "data_quality": C.rnd(dq_factor),
        },
        "can_only_shrink": True,
    }


def shrink(value, evidence):
    v = C.num(value)
    if v is None:
        return None
    f = C.num((evidence or {}).get("confidence_factor"))
    return 0.0 if f is None else v * C.clamp(f)
