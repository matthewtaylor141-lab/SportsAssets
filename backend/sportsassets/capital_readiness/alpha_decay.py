"""Alpha half-life / regime decay estimator (pure, SHADOW).

Learns whether realized incremental value decays with signal age or capital
scale. It is intentionally conservative: insufficient evidence returns
UNAVAILABLE and never manufactures a half-life.
"""
from __future__ import annotations

import math
from . import common as C

VERSION = "ALPHA_DECAY_OBSERVATORY_V1"
MIN_POINTS = 20


def estimate(observations: list[dict]):
    rows = []
    for r in observations:
        age = C.num(r.get("signal_age_s"))
        alpha = C.num(r.get("realized_alpha_per_dollar"))
        if age is not None and alpha is not None and age >= 0:
            rows.append((age, alpha))
    if len(rows) < MIN_POINTS:
        return C.envelope("UNAVAILABLE", "INSUFFICIENT_ALPHA_DECAY_OBSERVATIONS",
                          sample_n=len(rows), half_life_s=None, version=VERSION)
    # Compare lower/upper age halves; do not overfit an exponential curve.
    rows.sort()
    mid = len(rows)//2
    young, old = rows[:mid], rows[mid:]
    my = C.mean(v for _, v in young)
    mo = C.mean(v for _, v in old)
    ay = C.mean(a for a, _ in young)
    ao = C.mean(a for a, _ in old)
    half = None
    if my is not None and mo is not None and my > 0 and 0 < mo < my and ao > ay:
        ratio = mo / my
        half = (ao - ay) * math.log(0.5) / math.log(ratio)
    return C.envelope("OK", None, sample_n=len(rows),
                      young_mean_alpha_per_dollar=C.rnd(my),
                      old_mean_alpha_per_dollar=C.rnd(mo),
                      young_mean_age_s=C.rnd(ay), old_mean_age_s=C.rnd(ao),
                      half_life_s=C.rnd(half),
                      decay_detected=(half is not None), version=VERSION)
