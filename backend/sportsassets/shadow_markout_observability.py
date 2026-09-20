"""WHICH MARKOUT HORIZONS THE CAPTURE GRID CAN ACTUALLY RESOLVE.

Owner directive 2026-09-20, from an independent measurement of the
direct institutional L2 evidence cadence:

    P50 ~= 60.95s   P95 ~= 63.98s   MAX ~= 64.70s

    "30S_MARKOUT_STATUS =
     UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY.
     Do not tune a tolerance around the results."

THE RULE IS STRUCTURAL, NOT FITTED. Nothing here was chosen by looking
at what the 30S numbers came out as. The test is a property of the
horizon and its tolerance:

    a horizon is UNOBSERVABLE when  tolerance >= horizon

because the admissible window is [target - tolerance, target +
tolerance], and once tolerance reaches the horizon that window's lower
bound is THE DECISION INSTANT ITSELF. A "30-second markout" is then
permitted to be measured against the entry book, at zero elapsed time
-- which is not a markout, it is the entry price wearing a later
label. No amount of capture is going to fix that, and no threshold
here is adjusted to make it pass.

WHY THE CAPTURE FREQUENCY IS THE OPERATIVE CAUSE ANYWAY, and why the
owner's name for it is the right one. The 30s tolerance FLOOR exists
because books arrive about a minute apart; with a ~61s grid a tighter
floor would simply turn every short-horizon markout into a miss. So
the tolerance is what it is because of the capture cadence, and the
cadence is what makes the 30s horizon unresolvable. If books ever
arrive every two seconds the floor can come down, the rule below will
say OBSERVABLE without being edited, and the existing rows will still
read exactly as they were written.

TWO SEPARATE FACTS, KEPT SEPARATE:

  status            can this horizon be distinguished from the entry
                    instant at all? (structural)
  captureGuaranteed is the admissible window wider than the measured
                    grid period, so that a book is CERTAIN to fall in
                    it? (empirical)

60S passes the first and FAILS the second -- its window is 60s wide
against a 60.95s P50 grid, so a 60S markout is resolvable but not
guaranteed to land. That is reported rather than hidden, because a
reader comparing 60S and 300S should know one of them is marginal.

NOTHING HERE REWRITES A ROW. The classification is computed when the
rows are READ. Every markout ever written stays exactly as it was
written -- not deleted, not rewritten, not converted to zero -- and
stops being eligible for a performance conclusion instead.
"""

from __future__ import annotations

# ── the measured grid, and where the numbers came from ───────────────
#
# Owner's independent measurement of bettor_l2_evidence arrival spacing
# under DIRECT_INSTITUTIONAL_WORKER, 2026-09-20. Recorded as data with
# its provenance rather than as a tunable: a later measurement replaces
# these and the verdicts move with it.

CAPTURE_MEASURED_AT = "2026-09-20"
CAPTURE_MEASURED_BY = "owner, independent production query"
CAPTURE_REGIME = "DIRECT_INSTITUTIONAL_WORKER"
CAPTURE_P50_S = 60.95
CAPTURE_P95_S = 63.98
CAPTURE_MAX_S = 64.70

OBSERVABLE = "OBSERVABLE"
UNOBSERVABLE = "UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY"


def observability(horizon_s, tolerance_s, *, capture_p50_s=CAPTURE_P50_S,
                  capture_p95_s=CAPTURE_P95_S) -> dict:
    """Can this horizon be resolved, and is a capture guaranteed?

    Returns a verdict either way. A horizon nobody can measure is a
    real finding about the feed, not an error.
    """
    horizon_s = float(horizon_s)
    tolerance_s = float(tolerance_s)

    # THE STRUCTURAL TEST. When the tolerance reaches the horizon the
    # admissible window opens at the decision instant, so the
    # "markout" may be the entry book at zero elapsed time.
    resolvable = tolerance_s < horizon_s
    earliest_elapsed_s = max(horizon_s - tolerance_s, 0.0)

    # THE EMPIRICAL ONE. A grid of period P is certain to place a
    # capture inside any window of width >= P. The window here is
    # 2 * tolerance, clipped at the decision instant.
    window_s = min(2.0 * tolerance_s, horizon_s + tolerance_s)
    guaranteed = window_s >= float(capture_p95_s)

    status = OBSERVABLE if resolvable else UNOBSERVABLE
    if resolvable:
        why = None if guaranteed else (
            "resolvable but NOT guaranteed: the admissible window is "
            "%.0fs wide against a %.2fs P95 capture grid, so a markout "
            "at this horizon can legitimately find no book and be "
            "recorded as a miss" % (window_s, capture_p95_s))
    else:
        why = (
            "the %.0fs tolerance reaches the %.0fs horizon, so the "
            "admissible window opens at the decision instant and this "
            "markout may be measured against the entry book at zero "
            "elapsed time. The tolerance floor is %.0fs because direct "
            "institutional books arrive about %.0fs apart, so a %.0fs "
            "horizon cannot be resolved on this capture grid."
            % (tolerance_s, horizon_s, tolerance_s, capture_p50_s,
               horizon_s))

    return {
        "horizonS": horizon_s,
        "toleranceS": tolerance_s,
        "status": status,
        "resolvable": resolvable,
        "captureGuaranteed": guaranteed,
        "earliestElapsedS": earliest_elapsed_s,
        "admissibleWindowS": window_s,
        "capture": {"p50S": capture_p50_s, "p95S": capture_p95_s,
                    "maxS": CAPTURE_MAX_S, "regime": CAPTURE_REGIME,
                    "measuredAt": CAPTURE_MEASURED_AT,
                    "measuredBy": CAPTURE_MEASURED_BY},
        "why": why,
        # §: what this status MEANS for a reader, on the row itself.
        "eligibleForPerformance": resolvable,
    }


def by_horizon(horizons=None) -> dict:
    """{horizon name: verdict} for the lane's frozen horizon set."""
    from . import shadow_experimental_markouts as mk

    out = {}
    for name, seconds in (horizons or mk.HORIZONS):
        out[name] = observability(seconds, mk.tolerance_s(seconds))
    return out


def observable_horizons() -> tuple:
    """The horizon NAMES a performance number may be built from."""
    return tuple(name for name, v in by_horizon().items()
                 if v["eligibleForPerformance"])


def unobservable_horizons() -> tuple:
    return tuple(name for name, v in by_horizon().items()
                 if not v["eligibleForPerformance"])
