"""Section 1. THE canonical delta sign convention. There is no other.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

We have already had one verbal sign reversal reach a report. The fix is not to
be more careful; it is to have exactly one orientation, defined in one place,
emitted by one function, and pinned by tests that build an obviously better and
an obviously worse challenger and assert which way the number points.

    DELTA_LOG_LOSS = LOG_LOSS_B0 - LOG_LOSS_CHALLENGER
    DELTA_BRIER    = BRIER_B0    - BRIER_CHALLENGER

    POSITIVE  =  CHALLENGER IMPROVES ON B0
    ZERO      =  EQUAL
    NEGATIVE  =  CHALLENGER WORSE

The mnemonic that makes it stick: these are LOSSES, so the challenger's number
is the one being subtracted, and a challenger that loses less produces a
positive delta. "Higher is better" holds for the delta even though "lower is
better" holds for each score.

This applies everywhere without exception: standalone, incremental blend,
ensemble, subgroups, reports, tables and confidence intervals. A confidence
interval is reported in the same orientation as its point estimate, so
CI_LOW > 0 means the challenger improved.

No other delta orientation may be emitted. Anything computing a difference of
proper scores calls `delta()` or `delta_from_scores()`; nothing subtracts by
hand.
"""

import math
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

CANONICAL_DELTA_DEFINITION = "SCORE_B0 - SCORE_CHALLENGER"
POSITIVE_MEANS = "CHALLENGER_IMPROVES_ON_B0"
ZERO_MEANS = "EQUAL"
NEGATIVE_MEANS = "CHALLENGER_WORSE_THAN_B0"

SIGN_CONVENTION_IS_CANONICAL = True
NO_OTHER_DELTA_ORIENTATION_MAY_BE_EMITTED = True

APPLIES_TO = ("STANDALONE", "INCREMENTAL_BLEND", "ENSEMBLE", "SUBGROUPS",
              "REPORTS", "TABLES", "CONFIDENCE_INTERVALS")

WHY_THIS_EXISTS = (
    "a V3 report described negative deltas as sitting on the improving side. "
    "They meant the opposite. One definition, one function, and tests that "
    "assert the direction on a deliberately-better and a deliberately-worse "
    "challenger are cheaper than remembering")


def _clip(p, eps=1e-6):
    return min(max(float(p), eps), 1.0 - eps)


def log_loss(p, y):
    p = _clip(p)
    return -math.log(p) if y else -math.log(1.0 - p)


def brier(p, y):
    return (_clip(p) - float(y)) ** 2


SCORERS = {"LOG_LOSS": log_loss, "BRIER": brier}


def delta_from_scores(score_b0, score_challenger):
    """The one place the subtraction happens."""
    if score_b0 is None or score_challenger is None:
        return NOT_IDENTIFIED
    return float(score_b0) - float(score_challenger)


def interpret(d, tol=0.0):
    if d == NOT_IDENTIFIED or d is None:
        return NOT_IDENTIFIED
    if d > tol:
        return POSITIVE_MEANS
    if d < -tol:
        return NEGATIVE_MEANS
    return ZERO_MEANS


def event_scores(rows, pkey, ykey="Y", event_key="EVENT_KEY",
                 score="LOG_LOSS"):
    """Within-event mean of a proper score, the frozen aggregation rule."""
    fn = SCORERS[score]
    by = defaultdict(list)
    for r in rows:
        p, y = r.get(pkey), r.get(ykey)
        if p is None or y not in (0, 1):
            continue
        by[r[event_key]].append(fn(p, y))
    return {e: sum(v) / len(v) for e, v in by.items()}


def delta(rows, challenger, b0="P_MARKET", ykey="Y", event_key="EVENT_KEY",
          score="LOG_LOSS"):
    """Event-equal weighted canonical delta, paired on the rows both priced.

    Returns the delta AND the per-event paired differences in the SAME
    orientation, so no caller ever has to flip a sign to combine them.
    """
    fn = SCORERS[score]
    by = defaultdict(list)
    dropped = 0
    for r in rows:
        pc, pb, y = r.get(challenger), r.get(b0), r.get(ykey)
        if y not in (0, 1) or pc is None or pb is None:
            dropped += 1
            continue
        by[r[event_key]].append((fn(pb, y), fn(pc, y)))
    if not by:
        return {"STATUS": "NO_PAIRED_ROWS", "DELTA": NOT_IDENTIFIED}
    per_event, s_b0, s_ch = {}, [], []
    for e, prs in by.items():
        n = len(prs)
        b = sum(x for x, _ in prs) / n
        c = sum(x for _, x in prs) / n
        per_event[e] = delta_from_scores(b, c)
        s_b0.append(b)
        s_ch.append(c)
    mb = sum(s_b0) / len(s_b0)
    mc = sum(s_ch) / len(s_ch)
    d = delta_from_scores(mb, mc)
    return {
        "STATUS": "MEASURED",
        "SCORE": score,
        "CHALLENGER": challenger,
        "B0": b0,
        "SCORE_B0": mb,
        "SCORE_CHALLENGER": mc,
        "DELTA": d,
        "DELTA_PER_EVENT": per_event,
        "INTERPRETATION": interpret(d),
        "EVENTS": len(per_event),
        "ROWS_PAIRED": sum(len(v) for v in by.values()),
        "ROWS_DROPPED_UNPAIRED": dropped,
        "CONVENTION": CANONICAL_DELTA_DEFINITION,
        "POSITIVE_MEANS": POSITIVE_MEANS,
        "WEIGHTING": "EVENT_EQUAL_WEIGHTED",
    }


def delta_both_scores(rows, challenger, b0="P_MARKET", **kw):
    """DELTA_LOG_LOSS and DELTA_BRIER together, same orientation."""
    return {
        "DELTA_LOG_LOSS": delta(rows, challenger, b0, score="LOG_LOSS", **kw),
        "DELTA_BRIER": delta(rows, challenger, b0, score="BRIER", **kw),
        "CONVENTION": CANONICAL_DELTA_DEFINITION,
        "POSITIVE_MEANS": POSITIVE_MEANS,
    }


def format_delta(d, places=5):
    """A delta is always printed with an explicit sign. No bare numbers."""
    if d == NOT_IDENTIFIED or d is None:
        return NOT_IDENTIFIED
    return ("%+." + str(places) + "f") % d


def describe():
    return {
        "CANONICAL_DELTA_DEFINITION": CANONICAL_DELTA_DEFINITION,
        "DELTA_LOG_LOSS": "LOG_LOSS_B0 - LOG_LOSS_CHALLENGER",
        "DELTA_BRIER": "BRIER_B0 - BRIER_CHALLENGER",
        "POSITIVE_MEANS": POSITIVE_MEANS,
        "ZERO_MEANS": ZERO_MEANS,
        "NEGATIVE_MEANS": NEGATIVE_MEANS,
        "APPLIES_TO": APPLIES_TO,
        "NO_OTHER_DELTA_ORIENTATION_MAY_BE_EMITTED":
            NO_OTHER_DELTA_ORIENTATION_MAY_BE_EMITTED,
        "WHY_THIS_EXISTS": WHY_THIS_EXISTS,
    }
