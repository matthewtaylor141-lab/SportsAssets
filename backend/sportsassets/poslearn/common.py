"""Shared helpers for the profitability evaluation-and-learning layer
(migration 218). Pure; no I/O.

REUSES sportsassets/intel/common.py (the SHADOW envelope, the null-with-a-
reason `Out` map, number helpers) rather than redefining them. Adds the
canonical-document hashing every registration uses, the normal
distribution helpers the predeclared tests need (no numpy in the image:
requirements.lock has none), and the multiple-hypothesis adjustment.
"""
from __future__ import annotations

import hashlib
import json
import math

from ..intel import common as IC

LABEL = IC.LABEL                          # 'SHADOW'
AUTHORITY = "SHADOW_RESEARCH_NO_AUTHORITY"
DISCLOSURE = (
    "SHADOW / RESEARCH: registered, forecast, scored and displayed only. No "
    "authority over production probabilities, thresholds, sizing, "
    "allowlists, orders or capital; nothing in production reads these "
    "tables. Promotion needs predeclared criteria met, a Karen challenge, an "
    "independent Audrey evaluation and a HUMAN approval record no agent can "
    "write -- and even that changes nothing until a separate owner release. "
    "Unmeasured is null / UNAVAILABLE / UNPROVEN, never 0.")
RUNNER_ACTOR = "POS_LEARN_RUNNER"

num = IC.num
rnd = IC.rnd
mean = IC.mean
stdev = IC.stdev
median = IC.median
epoch = IC.epoch
jload = IC.jload
clamp = IC.clamp
Out = IC.Out


def envelope(**extra) -> dict:
    out = {"label": LABEL, "authority": AUTHORITY, "disclosure": DISCLOSURE}
    out.update(extra)
    return out


def canonical(obj) -> str:
    """The one canonical text of a document: sorted keys, no whitespace.
    Postgres re-parses it to jsonb; the CHECK compares document = text."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def logit(p, eps=1e-6):
    q = min(max(float(p), eps), 1.0 - eps)
    return math.log(q / (1.0 - q))


def sigmoid(z):
    z = float(z)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def norm_cdf(x) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def norm_ppf(q) -> float:
    """Inverse standard normal CDF by bisection on erf (exact to ~1e-12;
    a handful of calls per cycle, so speed does not matter)."""
    q = float(q)
    if not 0.0 < q < 1.0:
        raise ValueError("norm_ppf needs 0 < q < 1")
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if norm_cdf(mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def adjusted_alpha(alpha: float, family_size: int) -> float:
    """MULTIPLE HYPOTHESES: Bonferroni over the family size RECORDED AT
    REGISTRATION (never the number that happened to look good)."""
    return float(alpha) / max(1, int(family_size))


def adjusted_z(alpha: float, family_size: int, *, two_sided=True) -> float:
    a = adjusted_alpha(alpha, family_size)
    return norm_ppf(1.0 - (a / 2.0 if two_sided else a))


def mean_ci(xs, z):
    """(mean, lo, hi, n) of xs with a normal interval at z; Nones when
    fewer than two values (one value has no sampling distribution)."""
    xs = [float(x) for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return None, None, None, 0
    m = sum(xs) / n
    if n < 2:
        return m, None, None, n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    se = sd / math.sqrt(n)
    return m, m - z * se, m + z * se, n


def band(x, edges, labels, unknown="UNKNOWN"):
    if x is None:
        return unknown
    for i, e in enumerate(edges):
        if x < e:
            return labels[i]
    return labels[len(edges)]
