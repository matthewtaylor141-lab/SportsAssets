"""Section 21. Power and precision from PAIRED EVENT-LEVEL score differences.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

The question this module answers is:

    How many independent EVENTS are needed to distinguish a given improvement
    over B0?

It is NOT:

    How many contract observations are needed?

Those two questions have different answers, and the second one flatters the
experiment. A single fixture can carry ninety-two contract rows -- a moneyline,
a draw, a dozen rungs of a totals ladder, a block of exact scores -- and those
rows are not ninety-two independent tries at the same question. They are one
match, priced ninety-two ways, sharing one realised scoreline. Estimating
variance from rows treats that one match as ninety-two, and the resulting
sample-size requirement comes out far too small.

So the unit here is the EVENT, and the object is the PAIRED DIFFERENCE

    D_EVENT = PROPER_SCORE_CHALLENGER(event) - PROPER_SCORE_B0(event)

with the frozen event-level aggregation rule applied FIRST -- mean over the
event's rows -- and the difference taken AFTER. Pairing is what preserves the
within-event dependence: whatever made a fixture hard (a red card, a freak
scoreline) hits both forecasters, and differencing inside the event removes it
instead of counting it as noise in both.
"""

import math
import random
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

POWER_VARIANCE_SOURCE = "PAIRED_EVENT_LEVEL_SCORE_DIFFERENCES"

FORBIDDEN_VARIANCE_SOURCES = (
    "RAW_OUTCOME_VARIANCE",
    "CONTRACT_ROW_VARIANCE",
    "UNPAIRED_EVENT_VARIANCE",
)

WHY_THOSE_ARE_FORBIDDEN = (
    "RAW_OUTCOME_VARIANCE answers a question about outcomes, not about the "
    "difference between two forecasters of those outcomes. CONTRACT_ROW_"
    "VARIANCE treats correlated rows within one fixture as independent trials "
    "and understates the required sample, sometimes by an order of magnitude. "
    "UNPAIRED_EVENT_VARIANCE is the subtler error: it takes SD(challenger) and "
    "SD(baseline) separately and loses the fact that both saw the same match, "
    "so it inflates the requirement instead. Only the paired difference is "
    "both at the right unit and free of the shared per-fixture component."
)

WITHIN_EVENT_DEPENDENCE_PRESERVED = True

THE_QUESTION = (
    "How many independent events are needed to distinguish a given "
    "improvement over B0?")

THE_QUESTION_IT_IS_NOT = (
    "How many contract observations are needed?")

# Two-sided normal-approximation constants. The bootstrap path below does not
# use these; it is reported beside them precisely because D_EVENT need not be
# normal.
Z_ALPHA_TWO_SIDED_95 = 1.959963984540054
Z_POWER = {0.80: 0.8416212335729143, 0.90: 1.2815515655446004,
           0.95: 1.6448536269514722}


def _clip(p, eps=1e-6):
    return min(max(float(p), eps), 1.0 - eps)


def _log_loss(p, y):
    p = _clip(p)
    return -math.log(p) if y else -math.log(1.0 - p)


def _brier(p, y):
    return (_clip(p) - float(y)) ** 2


SCORERS = {"LOG_LOSS": _log_loss, "BRIER": _brier}


def paired_event_differences(rows, challenger, baseline="P_MARKET",
                             ykey="Y", event_key="EVENT_KEY",
                             score="LOG_LOSS"):
    """D_EVENT for every event, on the rows BOTH forecasters actually priced.

    The intersection matters. If the challenger could price only the moneyline
    of a fixture whose market priced sixty rows, then comparing the
    challenger's one-row mean against the market's sixty-row mean is not a
    paired difference at all -- it is two different questions subtracted. Rows
    either forecaster left unpriced are dropped from BOTH sides, and the count
    of what was dropped is returned rather than absorbed.
    """
    fn = SCORERS[score]
    by = defaultdict(list)
    dropped = 0
    for r in rows:
        pc, pb, y = r.get(challenger), r.get(baseline), r.get(ykey)
        if y not in (0, 1) or pc is None or pb is None:
            dropped += 1
            continue
        by[r[event_key]].append((fn(pc, y), fn(pb, y)))
    diffs = {}
    for ev, prs in by.items():
        n = len(prs)
        sc = sum(a for a, _ in prs) / n
        sb = sum(b for _, b in prs) / n
        diffs[ev] = sc - sb
    return diffs, {"EVENTS": len(diffs),
                   "ROWS_PAIRED": sum(len(v) for v in by.values()),
                   "ROWS_DROPPED_UNPAIRED": dropped,
                   "SCORE": score,
                   "CHALLENGER": challenger,
                   "BASELINE": baseline,
                   "AGGREGATION": "WITHIN_EVENT_MEAN_THEN_DIFFERENCE",
                   "SIGN_CONVENTION":
                       "NEGATIVE_D_EVENT_MEANS_THE_CHALLENGER_SCORED_BETTER"}


def _mean(v):
    return sum(v) / len(v) if v else None


def _sd(v):
    n = len(v)
    if n < 2:
        return None
    m = _mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))


def paired_summary(diffs, draws=2000, seed=20260917):
    """Location, spread and shape of D_EVENT, with a bootstrap interval.

    The bootstrap resamples EVENTS, which is the independent unit. It is
    reported beside the normal-theory interval because D_EVENT is a difference
    of log losses and can be strongly right-skewed: one fixture where the
    challenger was confidently wrong dominates the tail.
    """
    d = sorted(diffs.values()) if isinstance(diffs, dict) else sorted(diffs)
    n = len(d)
    if n < 2:
        return {"STATUS": "TOO_FEW_EVENTS", "N_EVENTS": n}
    m, s = _mean(d), _sd(d)
    se = s / math.sqrt(n)
    skew = NOT_IDENTIFIED
    if s and n > 2:
        skew = (sum((x - m) ** 3 for x in d) / n) / (s ** 3)
    rnd = random.Random(seed)
    boot = []
    for _ in range(draws):
        boot.append(_mean([d[rnd.randrange(n)] for _ in range(n)]))
    boot.sort()
    lo = boot[int(0.025 * draws)]
    hi = boot[int(0.975 * draws) - 1]
    return {
        "N_EVENTS": n,
        "MEAN_D_EVENT": m,
        "SD_D_EVENT": s,
        "SE_D_EVENT": se,
        "SKEW_D_EVENT": skew,
        "CI95_NORMAL": (m - Z_ALPHA_TWO_SIDED_95 * se,
                        m + Z_ALPHA_TWO_SIDED_95 * se),
        "CI95_EVENT_BOOTSTRAP": (lo, hi),
        "BOOTSTRAP_DRAWS": draws,
        "MIN_D_EVENT": d[0],
        "MAX_D_EVENT": d[-1],
        "MEDIAN_D_EVENT": d[n // 2],
        "VARIANCE_SOURCE": POWER_VARIANCE_SOURCE,
        "WITHIN_EVENT_DEPENDENCE_PRESERVED": WITHIN_EVENT_DEPENDENCE_PRESERVED,
    }


def events_required(delta, sd, power=0.80, alpha=0.05):
    """Events needed to detect a paired improvement of `delta` at `power`.

    Paired one-sample form: N = (z_alpha + z_power)^2 * sd^2 / delta^2, where
    sd is the SD of D_EVENT -- not of either forecaster's score, and not of
    the outcome.
    """
    if not sd or not delta:
        return NOT_IDENTIFIED
    if alpha != 0.05 or power not in Z_POWER:
        return NOT_IDENTIFIED
    z = Z_ALPHA_TWO_SIDED_95 + Z_POWER[power]
    return int(math.ceil((z ** 2) * (sd ** 2) / (float(delta) ** 2)))


def events_for_ci_width(delta, sd):
    """Events needed for a 95% interval half-width of `delta`."""
    if not sd or not delta:
        return NOT_IDENTIFIED
    return int(math.ceil((Z_ALPHA_TWO_SIDED_95 ** 2) * (sd ** 2)
                         / (float(delta) ** 2)))


LADDER_DELTAS = (0.002, 0.005, 0.010, 0.020)


def ladder_from_paired(diffs, deltas=LADDER_DELTAS, power=0.80):
    """The evidence ladder, derived from THIS sample's paired differences."""
    summ = paired_summary(diffs)
    if summ.get("STATUS"):
        return {"STATUS": summ["STATUS"], "LADDER": {}}
    sd = summ["SD_D_EVENT"]
    return {
        "SD_D_EVENT": sd,
        "N_EVENTS_OBSERVED": summ["N_EVENTS"],
        "LADDER": {d: {"EVENTS_FOR_POWER": events_required(d, sd, power),
                       "EVENTS_FOR_95_PCT_CI": events_for_ci_width(d, sd)}
                   for d in deltas},
        "POWER": power,
        "VARIANCE_SOURCE": POWER_VARIANCE_SOURCE,
        "SHORTFALL_FACTOR": {
            d: (events_required(d, sd, power) / summ["N_EVENTS"]
                if summ["N_EVENTS"] else NOT_IDENTIFIED)
            for d in deltas},
    }


def row_level_sd_forbidden(rows, challenger, baseline="P_MARKET", ykey="Y",
                           score="LOG_LOSS"):
    """The CONTRACT_ROW_VARIANCE estimate. Computed only to show it is wrong.

    This function exists so the error has a name and a number rather than
    being a warning in a comment. Nothing in the programme may use its output
    as a power input; `contrast_with_forbidden` reports how far off it is.
    """
    fn = SCORERS[score]
    d = []
    for r in rows:
        pc, pb, y = r.get(challenger), r.get(baseline), r.get(ykey)
        if y not in (0, 1) or pc is None or pb is None:
            continue
        d.append(fn(pc, y) - fn(pb, y))
    return {"ROWS": len(d), "SD_ROW_LEVEL": _sd(d),
            "THIS_MAY_NOT_BE_USED_FOR_POWER": True,
            "REASON": "rows within an event are not independent trials"}


def within_event_correlation(rows, challenger, baseline="P_MARKET",
                             ykey="Y", event_key="EVENT_KEY",
                             score="LOG_LOSS"):
    """Intraclass correlation of the ROW-level differences within an event.

    This is the quantity that decides how badly the row accounting misleads.
    If rows within a fixture were independent (rho = 0), counting rows would
    be harmless. They are not: a challenger prices every contract on a fixture
    from ONE score grid, so a grid that is wrong for that fixture is wrong on
    all of its rows in the same direction. That shared error is what rho
    measures, and it is why 932 rows are not 932 tries.
    """
    fn = SCORERS[score]
    by = defaultdict(list)
    for r in rows:
        pc, pb, y = r.get(challenger), r.get(baseline), r.get(ykey)
        if y not in (0, 1) or pc is None or pb is None:
            continue
        by[r[event_key]].append(fn(pc, y) - fn(pb, y))
    groups = [v for v in by.values() if len(v) >= 2]
    if len(groups) < 2:
        return NOT_IDENTIFIED
    allv = [x for g in groups for x in g]
    gm = _mean(allv)
    n_tot = len(allv)
    k = n_tot / len(groups)
    ss_between = sum(len(g) * (_mean(g) - gm) ** 2 for g in groups)
    ss_within = sum((x - _mean(g)) ** 2 for g in groups for x in g)
    df_b, df_w = len(groups) - 1, n_tot - len(groups)
    if df_b <= 0 or df_w <= 0:
        return NOT_IDENTIFIED
    ms_b, ms_w = ss_between / df_b, ss_within / df_w
    denom = ms_b + (k - 1) * ms_w
    if denom <= 0:
        return NOT_IDENTIFIED
    return max(0.0, (ms_b - ms_w) / denom)


def contrast_with_forbidden(rows, challenger, baseline="P_MARKET",
                            delta=0.010, ykey="Y", event_key="EVENT_KEY",
                            score="LOG_LOSS"):
    """Side by side: the correct accounting, and the flattering wrong one.

    The misleading number is NOT the raw requirement -- rows and events are
    different units, so the two counts are not directly comparable. It is the
    SHORTFALL FACTOR: required divided by held. Someone who estimates variance
    from rows will compare their requirement against the row count they have,
    and that comparison understates the shortfall by roughly 1 + (k-1)*rho.
    """
    diffs, meta = paired_event_differences(rows, challenger, baseline,
                                           ykey=ykey, event_key=event_key,
                                           score=score)
    summ = paired_summary(diffs)
    bad = row_level_sd_forbidden(rows, challenger, baseline, ykey, score)
    ok_sd = summ.get("SD_D_EVENT")
    bad_sd = bad.get("SD_ROW_LEVEL")
    n_ok = events_required(delta, ok_sd)
    n_bad = events_required(delta, bad_sd)
    held_events = meta["EVENTS"]
    held_rows = meta["ROWS_PAIRED"]
    short_ok = (n_ok / held_events
                if isinstance(n_ok, int) and held_events else NOT_IDENTIFIED)
    short_bad = (n_bad / held_rows
                 if isinstance(n_bad, int) and held_rows else NOT_IDENTIFIED)
    understated = (short_ok / short_bad
                   if isinstance(short_ok, float) and isinstance(short_bad, float)
                   and short_bad else NOT_IDENTIFIED)
    rho = within_event_correlation(rows, challenger, baseline, ykey,
                                   event_key, score)
    return {
        "DELTA": delta,
        "PAIRED_EVENT_SD": ok_sd,
        "EVENTS_REQUIRED_CORRECT": n_ok,
        "EVENTS_HELD": held_events,
        "SHORTFALL_FACTOR_CORRECT": short_ok,
        "ROW_LEVEL_SD_FORBIDDEN": bad_sd,
        "ROWS_REQUIRED_IF_ROWS_WERE_USED": n_bad,
        "ROWS_HELD": held_rows,
        "SHORTFALL_FACTOR_IF_ROWS_WERE_USED": short_bad,
        "THE_FORBIDDEN_ACCOUNTING_UNDERSTATES_THE_SHORTFALL_BY": understated,
        "WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES": rho,
        "ROWS_PER_EVENT": (held_rows / held_events if held_events
                           else NOT_IDENTIFIED),
        "WHICH_ONE_GOVERNS": POWER_VARIANCE_SOURCE,
        "META": meta,
    }


# ---------------------------------------------------------------------------
# Section 22 (the second one): uncertainty AROUND the ladder.
#
# SD(D_EVENT) is itself an estimate from a small sample. Reporting "72 events"
# as though it were a constant repeats, one level up, exactly the mistake the
# paired-difference rule fixed: treating a noisy estimate as a fact.
#
# So the ladder is bootstrapped. Resample EVENTS (never rows), recompute the
# SD, recompute the requirement, and report the distribution. The prospective
# ladder then uses a CONSERVATIVE quantile rather than the point estimate,
# because being wrong about how much evidence you need is only expensive in one
# direction: you stop too early and call an underpowered null a result.
# ---------------------------------------------------------------------------

REQUIRED_N_QUANTILES = (0.50, 0.75, 0.90, 0.95)

LADDER_USES_QUANTILE = 0.90

WHY_A_CONSERVATIVE_QUANTILE = (
    "the cost of over-estimating the requirement is delay. The cost of "
    "under-estimating it is declaring a negative result on a sample that could "
    "never have shown the effect. Those are not symmetric, so the prospective "
    "ladder uses the P90 requirement, not the point estimate")


def bootstrap_required_n(diffs, deltas=LADDER_DELTAS, power=0.80, draws=2000,
                         seed=20260917, quantiles=REQUIRED_N_QUANTILES):
    """Distribution of the required N, by resampling EVENTS.

    Each draw resamples the events with replacement, recomputes SD(D_EVENT),
    and converts that to a requirement. A draw whose SD is degenerate is
    dropped and counted rather than silently treated as zero.
    """
    d = list(diffs.values()) if isinstance(diffs, dict) else list(diffs)
    n = len(d)
    if n < 8:
        return {"STATUS": "TOO_FEW_EVENTS", "N_EVENTS": n}
    rnd = random.Random(seed)
    sds, degenerate = [], 0
    for _ in range(draws):
        samp = [d[rnd.randrange(n)] for _ in range(n)]
        s = _sd(samp)
        if not s:
            degenerate += 1
            continue
        sds.append(s)
    sds.sort()
    point_sd = _sd(d)
    out = {}
    for delta in deltas:
        reqs = sorted(events_required(delta, s, power) for s in sds)
        row = {"REQUIRED_N_POINT_ESTIMATE": events_required(delta, point_sd,
                                                            power)}
        for q in quantiles:
            idx = min(len(reqs) - 1, int(q * len(reqs)))
            label = ("REQUIRED_N_BOOTSTRAP_MEDIAN" if q == 0.50
                     else "REQUIRED_N_P%d" % int(q * 100))
            row[label] = reqs[idx]
        row["LADDER_VALUE"] = row["REQUIRED_N_P90"]
        out[delta] = row
    return {
        "STATUS": "MEASURED",
        "N_EVENTS": n,
        "POINT_SD_D_EVENT": point_sd,
        "BOOTSTRAP_SD_MEDIAN": sds[len(sds) // 2] if sds else NOT_IDENTIFIED,
        "BOOTSTRAP_SD_P90": (sds[int(0.90 * len(sds))] if sds
                             else NOT_IDENTIFIED),
        "DRAWS": draws,
        "DEGENERATE_DRAWS": degenerate,
        "RESAMPLING_UNIT": "EVENT_NOT_ROW",
        "BY_EFFECT_SIZE": out,
        "LADDER_USES_QUANTILE": LADDER_USES_QUANTILE,
        "WHY_A_CONSERVATIVE_QUANTILE": WHY_A_CONSERVATIVE_QUANTILE,
        "POWER_ESTIMATE_UNCERTAINTY_STATUS": "QUANTIFIED_BY_EVENT_BOOTSTRAP",
    }


def cluster_sensitivity(diffs, cluster_of, deltas=LADDER_DELTAS, power=0.80,
                        draws=2000, seed=20260917):
    """A more conservative ladder that resamples CLUSTERS, not events.

    Events are not fully independent either. Two fixtures in the same league on
    the same weekend share a model, a data vintage and often a weather system.
    If clusters are large enough to matter, resampling whole clusters widens
    the interval and raises the requirement. `cluster_of` maps an event key to
    its cluster label; a block bootstrap over those labels is the sensitivity.
    """
    if not isinstance(diffs, dict):
        return {"STATUS": "NEEDS_KEYED_DIFFS"}
    by = defaultdict(list)
    for ev, v in diffs.items():
        by[cluster_of(ev)].append(v)
    clusters = sorted(by)
    if len(clusters) < 4:
        return {"STATUS": "TOO_FEW_CLUSTERS", "CLUSTERS": len(clusters)}
    rnd = random.Random(seed)
    sds = []
    for _ in range(draws):
        samp = []
        for _ in range(len(clusters)):
            samp += by[clusters[rnd.randrange(len(clusters))]]
        s = _sd(samp)
        if s:
            sds.append(s)
    sds.sort()
    sizes = sorted(len(v) for v in by.values())
    out = {}
    for delta in deltas:
        reqs = sorted(events_required(delta, s, power) for s in sds)
        out[delta] = {
            "REQUIRED_N_BOOTSTRAP_MEDIAN": reqs[len(reqs) // 2],
            "REQUIRED_N_P90": reqs[int(0.90 * len(reqs))],
        }
    return {
        "STATUS": "MEASURED",
        "CLUSTERS": len(clusters),
        "EVENTS": len(diffs),
        "CLUSTER_SIZE_MEDIAN": sizes[len(sizes) // 2],
        "CLUSTER_SIZE_MAX": sizes[-1],
        "RESAMPLING_UNIT": "CLUSTER",
        "BY_EFFECT_SIZE": out,
        "THIS_IS_A_SENSITIVITY_NOT_THE_PRIMARY": True,
        "WHEN_IT_MATTERS": (
            "if the cluster ladder is materially above the event ladder, the "
            "events are not behaving independently and the event ladder is "
            "optimistic"),
    }


def describe():
    return {
        "POWER_VARIANCE_SOURCE": POWER_VARIANCE_SOURCE,
        "REQUIRED_N_QUANTILES": REQUIRED_N_QUANTILES,
        "LADDER_USES_QUANTILE": LADDER_USES_QUANTILE,
        "WHY_A_CONSERVATIVE_QUANTILE": WHY_A_CONSERVATIVE_QUANTILE,
        "FORBIDDEN_VARIANCE_SOURCES": FORBIDDEN_VARIANCE_SOURCES,
        "WHY_THOSE_ARE_FORBIDDEN": WHY_THOSE_ARE_FORBIDDEN,
        "WITHIN_EVENT_DEPENDENCE_PRESERVED": WITHIN_EVENT_DEPENDENCE_PRESERVED,
        "THE_QUESTION": THE_QUESTION,
        "THE_QUESTION_IT_IS_NOT": THE_QUESTION_IT_IS_NOT,
        "LADDER_DELTAS": LADDER_DELTAS,
    }
