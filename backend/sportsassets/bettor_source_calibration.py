"""IS THE EXTERNAL SOURCE CALIBRATED? THE MEASUREMENT, NOT A TABLE.

`external_source_calibration` is where an answer is stored. This is what
produces one. A table plus a hand-written passing row is not a
measurement -- it is an assertion wearing a schema -- and the risk
engine's MODEL_TRUST_DRIFT gate reads that row, so writing one by hand
would be exactly the placeholder this project keeps deleting.

WHAT IS BEING MEASURED. PINNACLE_DEVIG_V1 states, for a named contract at
a named instant, the probability that a named event occurs. Every one of
those statements is recorded in `external_valuations` at the moment it was
made. Some of those events have since happened or not. The question is
whether the stated probabilities, taken as a set, match the frequencies
that followed.

────────────────────────────────────────────────────────────────────
THREE DEFECTS THIS VERSION FIXES, ALL MINE, ALL FOUND IN REVIEW OF THE
RELEASED EVALUATOR. Each one let it return PASSED for evidence that does
not support the claim:

  1 DEPENDENT OBSERVATIONS COUNTED AS INDEPENDENT ONES. The unique-event
    key was (event_key, payout_event). On a two-way market BOTH payout
    statements describe ONE fixture, and their squared errors are
    perfectly negatively dependent -- 150 fixtures were counted as 300
    "unique events". The key is now the FIXTURE, and the count reported
    is a count of fixtures.

  2 AN IN-SAMPLE BASELINE, AND NO REQUIREMENT TO BEAT IT. The old code
    computed the base rate FROM THE EVALUATED OUTCOMES, reported
    `beats_base_rate`, and then ignored it in the verdict. 300 forecasts
    all saying 0.60 on fixtures that won 90% of the time score a Brier of
    0.18, pass a 0.24 ceiling, and are grossly miscalibrated -- a
    constant 0.90 scores 0.09. The baseline is now FIT ON A DISJOINT,
    EARLIER SET whose outcomes are never scored, and beating it by more
    than sampling noise is a REQUIRED condition.

  3 0.25 PRESENTED AS "THE NO-SKILL BENCHMARK". 0.25 is the score of a
    constant 0.50 forecast, and nothing else. It is not a universal
    no-skill floor: on a book of heavy favourites the no-skill score is
    far lower, which is precisely how defect 2 passed. It is kept as a
    labelled REFERENCE and is not a criterion.

EVERYTHING BELOW IS DECLARED BEFORE THE DATA IS LOOKED AT:

  SCOPE            which source version, which method, which sport
                   families and which market. A score computed over a
                   different scope is a different number.
  POINT IN TIME    the probability used is the one RECORDED at decision
                   time, never re-derived, and per fixture it is the
                   FIRST such record. Taking the last, or the best, or an
                   average would be choosing among a source's own
                   revisions after seeing which way the event went.
  INDEPENDENT UNIT one observation per FIXTURE. A source quoted every
                   fifteen minutes for six hours produces twenty-four
                   rows about one coin flip, and the two sides of a
                   two-way market are two statements about the same coin
                   flip. Neither adds a degree of freedom.
  OUTCOME CLASSES  RESOLVED / VOID / UNRESOLVED / UNVERIFIED. Only
                   RESOLVED is scored, and RESOLVED requires the venue's
                   own settlement provenance on the row: an outcome whose
                   basis is not recorded is UNVERIFIED and excluded. All
                   four counts are part of the result.
  BASELINE         a constant forecast fitted on the chronologically
                   EARLIEST third of resolved fixtures, which are then
                   excluded from scoring. Fixed without any evaluation
                   outcome, by construction.
  METRIC           BRIER on the evaluation set, reported WITH its
                   sampling uncertainty, and decomposed so that the
                   CALIBRATION component is visible separately from
                   discrimination.
  ACCEPTANCE       four conditions, ALL required. See ACCEPTANCE.
  REPRODUCIBLE     the result carries the inputs' own hash, so the same
                   rows produce the same answer and a different answer
                   means different rows.

WHAT A PASS DOES AND DOES NOT ESTABLISH. It establishes that, on the
declared scope, over the declared minimum number of independent fixtures,
the source's stated probabilities scored better than a baseline fitted
without them, by more than sampling noise, and that their binned
reliability error stayed inside a declared bound. It does NOT establish
that trading them is profitable: profitability depends on acquisition
cost, fees, fill and sizing, none of which appear here. Those live in the
entry lane and are a separate verdict.

A SHORTFALL IS A RESULT. With too few resolved fixtures the answer is
INSUFFICIENT, with the number still needed stated, and the gate stays
shut. That is the expected first answer and it is not a failure of the
measurement.
"""

from __future__ import annotations

import hashlib
import json
import math

from . import bettor_pinnacle_devig as devig

VERSION = "EXTERNAL_SOURCE_CALIBRATION_V2"

#: The version whose results are superseded. Rows written by V1 were
#: produced by the evaluator with the three defects above, so a reader
#: must be able to tell them apart from these.
SUPERSEDES = "EXTERNAL_SOURCE_CALIBRATION_V1"

# ── THE SCOPE ────────────────────────────────────────────────────────
SOURCE_VERSION = devig.VERSION
SOURCE_METHOD = devig.DEFAULT_METHOD
SOURCE_CLASS = devig.SOURCE_CLASS
SUPPORTED_FAMILIES = ("baseball", "soccer")
SUPPORTED_MARKET = "h2h"

SCOPE = {
    "source_version": SOURCE_VERSION,
    "source_method": SOURCE_METHOD,
    "source_class": SOURCE_CLASS,
    "families": list(SUPPORTED_FAMILIES),
    "market": SUPPORTED_MARKET,
    "independent_unit": "ONE_FIXTURE",
    "why_scope_matters": (
        "a Brier score over a different set of sports, markets or de-vig "
        "methods is a different number about a different thing. The gate "
        "reads one row, so the row has to say what it covers"),
}

# ── THE PREDECLARED METRIC AND CRITERIA ──────────────────────────────
METRIC = "BRIER"

#: The ceiling, and it is a POLICY THRESHOLD, not a proof of calibration.
#:
#: WHERE THE NUMBER COMES FROM. A constant 0.50 forecast scores exactly
#: 0.25 whatever the outcomes are, so 0.24 is "measurably better than
#: knowing nothing at all about a coin-flip market", while staying well
#: away from the 0.20-0.21 a genuinely sharp book reaches. It is a floor
#: on usefulness. It is NOT evidence of calibration on its own, and on a
#: book of heavy favourites it is trivially cleared by a badly
#: miscalibrated forecaster -- which is why conditions 3 and 4 exist.
BRIER_CEILING = 0.24

#: The score of a constant 0.50 forecast, exactly, for any outcome set:
#: (0.5 - o)^2 = 0.25 whether o is 0 or 1. Reported as a REFERENCE POINT.
#: It is not the no-skill score of an arbitrary market and is never a
#: criterion.
CONSTANT_HALF_REFERENCE = 0.25
CONSTANT_HALF_REFERENCE_IS = (
    "THE_SCORE_OF_A_CONSTANT_0.50_FORECAST_NOT_A_UNIVERSAL_NO_SKILL_FLOOR")

#: Independent FIXTURES required in the EVALUATION set -- not rows, not
#: payout statements, and not including the fixtures spent on fitting the
#: baseline. Chosen from what the comparison has to resolve: separating a
#: Brier difference of a few hundredths from sampling noise needs a few
#: hundred independent events. Declared in advance; the gate stays shut
#: until it is reached however good the early numbers look.
MIN_RESOLVED_EVENTS = 300

#: The chronologically earliest share of resolved fixtures reserved for
#: fitting the baseline. They are NEVER scored. A held-out baseline costs
#: sample, and that cost is stated rather than avoided by reusing the
#: evaluation outcomes.
BASELINE_FRACTION = 1.0 / 3.0
BASELINE_MIN_EVENTS = 50

#: Bins for the reliability table, and the bound the mean reliability
#: error must stay inside. 0.05 means "the stated probabilities are right
#: to within five points on average, bin by bin" -- a claim about
#: CALIBRATION specifically, which a Brier ceiling does not make.
CALIBRATION_BINS = 10
MAX_CALIBRATION_ERROR = 0.05

#: The confidence level at which the model must beat the held-out
#: baseline. Two-sided, applied to the PAIRED per-fixture difference in
#: squared error, which is the right uncertainty for a comparison on the
#: same events.
CONFIDENCE_Z = 1.959964

ACCEPTANCE = {
    "metric": METRIC,
    "all_required": True,
    "conditions": {
        "min_independent_fixtures": MIN_RESOLVED_EVENTS,
        "brier_at_or_below_ceiling": BRIER_CEILING,
        "beats_the_held_out_baseline": (
            "the paired difference in squared error must favour the source "
            "and its 95% confidence interval must exclude zero"),
        "calibration_error_at_or_below": MAX_CALIBRATION_ERROR,
    },
    "baseline": {
        "form": "A_CONSTANT_PROBABILITY",
        "fitted_on": "THE_CHRONOLOGICALLY_EARLIEST_THIRD_OF_RESOLVED_FIXTURES",
        "fitted_using_evaluation_outcomes": False,
        "those_fixtures_are_scored": False,
        "min_fixtures_to_fit": BASELINE_MIN_EVENTS,
    },
    "reference_only": {
        "constant_half_brier": CONSTANT_HALF_REFERENCE,
        "what_it_is": CONSTANT_HALF_REFERENCE_IS,
    },
    "the_ceiling_is": ("A_DECLARED_POLICY_THRESHOLD_NOT_PROOF_OF_"
                       "CALIBRATION"),
    "what_a_pass_does_not_establish": [
        "that trading the source is profitable -- acquisition cost, fees, "
        "fill probability and sizing are not inputs to this measurement",
        "that the source is calibrated outside the declared scope",
        "that the source will stay calibrated: this is a measurement of a "
        "past window and carries that window's dates",
    ],
    "declared_before_any_data_was_read": True,
}

# ── outcome classes ──────────────────────────────────────────────────
RESOLVED = "RESOLVED"
VOID = "VOID"
UNRESOLVED = "UNRESOLVED"
#: An outcome is on the row but its PROVENANCE is not. Rows joined before
#: the venue-side mapping was corrected are in this class, and migration
#: 118 reopened them; anything else here was written by something whose
#: settlement conversion this evaluator cannot vouch for.
UNVERIFIED = "UNVERIFIED_OUTCOME_PROVENANCE"

#: The outcome bases this evaluator accepts as the venue's own answer.
#: Kept in step with `workers.ext_pinnacle_loop`'s writer by name, and
#: deliberately NOT including its inferred or named-winner classes.
VERIFIED_BASES = ("VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME")
VOID_BASIS = "CONFIRMED_VOID"

# ── statuses ─────────────────────────────────────────────────────────
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
PASSED = "PASSED"
FAILED = "FAILED"
OUT_OF_SCOPE = "NO_ROWS_IN_SCOPE"


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


def in_scope(row) -> bool:
    """Is this valuation one the declared scope covers?"""
    return (str(row.get("version") or "") == SOURCE_VERSION
            and str(row.get("devig_method") or "") == SOURCE_METHOD
            and str(row.get("sport_family") or "") in SUPPORTED_FAMILIES
            and str(row.get("market") or "") == SUPPORTED_MARKET)


def classify(row) -> str:
    """RESOLVED, VOID, UNRESOLVED or UNVERIFIED for one valuation row.

    THE PROVENANCE IS PART OF THE CLASSIFICATION, not metadata about it.
    `outcome` alone cannot say whether the venue's settlement price was
    mapped onto the side actually held, and an outcome established by the
    uncorrected mapping is wrong exactly on the short legs. So a 0/1 is
    RESOLVED only when `outcome_basis` names a verified venue answer;
    otherwise it is UNVERIFIED and excluded from the measurement.

    A VOID IS ALSO ESTABLISHED BY ITS BASIS, not inferred from a missing
    outcome: migration 103 forbids a known outcome that is neither 0 nor
    1, so a void is recorded as a basis with `outcome_known` still false.
    """
    basis = str(row.get("outcome_basis") or "")
    if basis == VOID_BASIS:
        return VOID
    if not row.get("outcome_known"):
        return UNRESOLVED
    o = row.get("outcome")
    try:
        o = int(o)
    except (TypeError, ValueError):
        return UNVERIFIED
    if o not in (0, 1):
        return UNVERIFIED
    return RESOLVED if basis in VERIFIED_BASES else UNVERIFIED


def unique_fixtures(rows) -> dict:
    """One observation per FIXTURE, and it is the FIRST one.

    Keyed on `event_key` alone. THE DEFECT THIS FIXES: the key used to
    include `payout_event`, so a two-way market contributed two rows --
    p and (1-p) against o and (1-o). Their squared errors are identical
    in magnitude and their information content is one fixture, so
    counting both inflated the sample by exactly a factor of two while
    adding no degrees of freedom. 150 fixtures were reported as 300
    "unique events".

    Earliest `observed_at` wins; ties break on the row id so the choice
    is deterministic and does not depend on row order. Which of a
    fixture's two payout statements survives is therefore decided by the
    clock, never by which one scored better.
    """
    best: dict = {}
    for r in rows:
        key = str(r.get("event_key") or "")
        if not key:
            continue
        cur = best.get(key)
        mine = (float(r.get("observed_at_epoch") or 0.0),
                int(r.get("id") or 0))
        if cur is None or mine < cur[0]:
            best[key] = (mine, r)
    return {k: v[1] for k, v in best.items()}


def _brier(pairs) -> float:
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def _mean_sd(xs):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return m, 0.0
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, math.sqrt(max(0.0, var))


def reliability(pairs, *, bins=CALIBRATION_BINS) -> dict:
    """The binned reliability table and the mean calibration error.

    THIS IS A DIFFERENT QUESTION FROM THE BRIER. A Brier mixes
    discrimination (does the forecaster separate winners from losers?)
    with reliability (when it says 0.60, do 60% happen?). A forecaster
    that says 0.60 on fixtures that win 90% of the time can score a
    perfectly respectable Brier while being wrong about every single
    probability it states -- which is the exact case the released
    evaluator admitted. So reliability is measured and reported on its
    own, and has its own acceptance condition.
    """
    width = 1.0 / float(bins)
    buckets: dict = {}
    for p, o in pairs:
        b = min(int(p / width), bins - 1)
        buckets.setdefault(b, []).append((p, o))
    n = len(pairs)
    table = []
    ece = 0.0
    worst = 0.0
    for b in sorted(buckets):
        got = buckets[b]
        mp = sum(p for p, _ in got) / len(got)
        mo = sum(o for _, o in got) / len(got)
        gap = abs(mp - mo)
        ece += (len(got) / n) * gap
        worst = max(worst, gap)
        table.append({"bin": b, "lo": round(b * width, 4),
                      "hi": round((b + 1) * width, 4),
                      "events": len(got),
                      "mean_probability": round(mp, 6),
                      "observed_frequency": round(mo, 6),
                      "gap": round(gap, 6)})
    return {"bins": bins, "table": table,
            "expected_calibration_error": round(ece, 6),
            "worst_bin_gap": round(worst, 6),
            "measures": "RELIABILITY_ONLY_NOT_DISCRIMINATION"}


def decompose(pairs) -> dict:
    """Murphy's decomposition: Brier = reliability - resolution + base.

    Stated because the three pieces answer three different questions and
    the single Brier answers none of them alone.
    """
    n = len(pairs)
    obar = sum(o for _, o in pairs) / n
    width = 1.0 / float(CALIBRATION_BINS)
    buckets: dict = {}
    for p, o in pairs:
        buckets.setdefault(min(int(p / width), CALIBRATION_BINS - 1),
                           []).append((p, o))
    rel = res = 0.0
    for got in buckets.values():
        k = len(got)
        mp = sum(p for p, _ in got) / k
        mo = sum(o for _, o in got) / k
        rel += k * (mp - mo) ** 2
        res += k * (mo - obar) ** 2
    return {"reliability": round(rel / n, 6),
            "resolution": round(res / n, 6),
            "uncertainty": round(obar * (1.0 - obar), 6),
            "identity": "BRIER = RELIABILITY - RESOLUTION + UNCERTAINTY",
            "reliability_is": "LOWER_IS_BETTER_CALIBRATION",
            "resolution_is": "HIGHER_IS_BETTER_DISCRIMINATION",
            "uncertainty_is": ("THE_BASE_RATE_VARIANCE_OF_THIS_SET_WHICH_NO_"
                               "FORECASTER_CONTROLS")}


def compare_to_baseline(pairs, baseline_p) -> dict:
    """The PAIRED comparison against a constant baseline, with its
    uncertainty.

    Paired, because both forecasts are scored on the SAME fixtures and
    the per-fixture difference removes the variance that the choice of
    fixtures contributes. The interval on that difference is what decides
    whether an improvement is real, and reporting the two Briers without
    it is how a 0.005 edge on 300 events reads as skill.
    """
    diffs = [((baseline_p - o) ** 2) - ((p - o) ** 2) for p, o in pairs]
    mean, sd = _mean_sd(diffs)
    n = len(diffs)
    se = (sd / math.sqrt(n)) if n else 0.0
    lo = mean - CONFIDENCE_Z * se
    hi = mean + CONFIDENCE_Z * se
    return {
        "baseline_probability": round(float(baseline_p), 6),
        "baseline_brier": round(_brier([(baseline_p, o)
                                        for _, o in pairs]), 6),
        "paired_mean_improvement": round(mean, 6),
        "paired_se": round(se, 6),
        "ci_low": round(lo, 6), "ci_high": round(hi, 6),
        "confidence_z": CONFIDENCE_Z,
        "improvement_is_significant": bool(mean > 0.0 and lo > 0.0),
        "sign_convention": "POSITIVE_MEANS_THE_SOURCE_BEAT_THE_BASELINE",
    }


def evaluate(rows, *, measured_at=None) -> dict:
    """The calibration measurement. Pure: it reads rows and nothing else.

    `rows` are `external_valuations` records carrying at least: id,
    version, devig_method, sport_family, market, event_key, payout_event,
    probability, observed_at_epoch, outcome_known, outcome, outcome_basis.
    """
    rows = list(rows or [])
    scoped = [r for r in rows if in_scope(r)]
    out = {
        "version": VERSION,
        "supersedes": SUPERSEDES,
        "scope": dict(SCOPE),
        "acceptance": dict(ACCEPTANCE),
        "measured_at": measured_at,
        "rows_considered": len(rows),
        "rows_in_scope": len(scoped),
        "rows_out_of_scope": len(rows) - len(scoped),
    }
    if not scoped:
        out.update(status=OUT_OF_SCOPE, within_tolerance=False,
                   why=("no recorded valuation is inside the declared "
                        "scope, so there is nothing to measure"))
        return out

    picked = unique_fixtures(scoped)
    out["unique_fixtures"] = len(picked)
    # NAMED AS WHAT IT IS. The old key produced this many "unique events";
    # reporting both makes the correction auditable rather than silent.
    out["payout_statements_in_scope"] = len(scoped)
    out["observations_collapsed"] = len(scoped) - len(picked)

    counts = {RESOLVED: 0, VOID: 0, UNRESOLVED: 0, UNVERIFIED: 0}
    usable = []
    for r in picked.values():
        cls = classify(r)
        counts[cls] += 1
        if cls != RESOLVED:
            continue
        p = r.get("probability")
        if p is None:
            # A RESOLVED FIXTURE WITH NO STATED PROBABILITY is not
            # scoreable and is not a zero. It moves to UNRESOLVED and is
            # counted, because pretending p=0 would punish the source for
            # a row it never filled in.
            counts[RESOLVED] -= 1
            counts[UNRESOLVED] += 1
            continue
        usable.append((float(r.get("observed_at_epoch") or 0.0),
                       int(r.get("id") or 0), float(p), int(r["outcome"])))

    out["outcome_counts"] = dict(counts)
    out["inputs_sha"] = _sha(sorted(
        (str(k), r.get("probability"), r.get("outcome"),
         r.get("outcome_known"), r.get("outcome_basis"))
        for k, r in picked.items()))

    # ── THE CHRONOLOGICAL SPLIT, BEFORE ANY SCORE IS COMPUTED ────────
    usable.sort()
    total = len(usable)
    out["resolved_fixtures"] = total
    k = int(total * BASELINE_FRACTION)
    fit = usable[:k]
    ev = usable[k:]
    out["baseline_split"] = {
        "fraction": round(BASELINE_FRACTION, 6),
        "fit_fixtures": len(fit),
        "evaluation_fixtures": len(ev),
        "order": "CHRONOLOGICAL_BY_OBSERVED_AT_THEN_ROW_ID",
        "fit_fixtures_are_scored": False,
        "total_resolved_fixtures_required": int(
            math.ceil(MIN_RESOLVED_EVENTS / (1.0 - BASELINE_FRACTION))),
    }
    pairs = [(p, o) for _, _, p, o in ev]

    # PROVISIONAL AND LABELLED. Reported so collection can be watched,
    # never so it can be read as a verdict.
    out["score_is_provisional"] = True
    out["provisional_brier"] = (None if not pairs
                                else round(_brier(pairs), 6))
    out["reference"] = {"constant_half_brier": CONSTANT_HALF_REFERENCE,
                        "what_it_is": CONSTANT_HALF_REFERENCE_IS}

    if len(fit) < BASELINE_MIN_EVENTS:
        out.update(
            status=INSUFFICIENT, within_tolerance=False, score=None,
            shortfall_events=max(0, MIN_RESOLVED_EVENTS - len(ev)),
            baseline_established=False,
            why=("the baseline needs %d resolved fixtures to fit and has "
                 "%d. A baseline fitted on the evaluated outcomes is not "
                 "a baseline, so no verdict is possible and the gate "
                 "stays shut" % (BASELINE_MIN_EVENTS, len(fit))))
        return out

    baseline_p = sum(o for _, _, _, o in fit) / len(fit)
    out["baseline_established"] = True
    out["baseline"] = {
        "probability": round(baseline_p, 6),
        "form": "A_CONSTANT_PROBABILITY",
        "fitted_on_fixtures": len(fit),
        "fitted_using_evaluation_outcomes": False,
        "why": ("a constant forecast equal to the base rate of an EARLIER, "
                "disjoint set of fixtures. It is the thing the source has "
                "to beat, and it knows nothing about the outcomes it is "
                "compared on"),
    }

    n = len(pairs)
    if n < MIN_RESOLVED_EVENTS:
        out.update(
            status=INSUFFICIENT, within_tolerance=False,
            score=out["provisional_brier"],
            shortfall_events=MIN_RESOLVED_EVENTS - n,
            shortfall_resolved_fixtures_total=max(
                0, out["baseline_split"]["total_resolved_fixtures_required"]
                - total),
            why=("%d independent resolved FIXTURES in the evaluation set; "
                 "%d are required. That needs %d resolved fixtures in "
                 "total, of which %d are held so far, because one in three "
                 "is spent fitting the baseline and is never scored. "
                 "Collection continues and the gate stays shut"
                 % (n, MIN_RESOLVED_EVENTS,
                    out["baseline_split"]["total_resolved_fixtures_required"],
                    total)))
        return out

    brier = _brier(pairs)
    se_each = [(p - o) ** 2 for p, o in pairs]
    _, sd = _mean_sd(se_each)
    se = sd / math.sqrt(n)
    rel = reliability(pairs)
    cmp_ = compare_to_baseline(pairs, baseline_p)
    dec = decompose(pairs)

    # ── THE FOUR CONDITIONS, EACH EVALUATED AND EACH REPORTED ────────
    checks = {
        "min_independent_fixtures": {
            "required": MIN_RESOLVED_EVENTS, "observed": n,
            "passed": n >= MIN_RESOLVED_EVENTS},
        "brier_at_or_below_ceiling": {
            "required": BRIER_CEILING, "observed": round(brier, 6),
            "passed": brier <= BRIER_CEILING},
        "beats_the_held_out_baseline": {
            "required": "paired improvement with a CI excluding zero",
            "observed": {k2: cmp_[k2] for k2 in
                         ("paired_mean_improvement", "ci_low", "ci_high",
                          "baseline_brier")},
            "passed": bool(cmp_["improvement_is_significant"])},
        "calibration_error_at_or_below": {
            "required": MAX_CALIBRATION_ERROR,
            "observed": rel["expected_calibration_error"],
            "passed": (rel["expected_calibration_error"]
                       <= MAX_CALIBRATION_ERROR)},
    }
    failed = [k2 for k2, v in checks.items() if not v["passed"]]
    ok = not failed

    out.update(
        status=(PASSED if ok else FAILED),
        within_tolerance=bool(ok),
        score=round(brier, 6),
        score_is_provisional=False,
        scored_events=n,
        uncertainty={
            "brier_standard_error": round(se, 6),
            "brier_ci_low": round(brier - CONFIDENCE_Z * se, 6),
            "brier_ci_high": round(brier + CONFIDENCE_Z * se, 6),
            "confidence_z": CONFIDENCE_Z,
            "basis": "SAMPLING_SE_OF_THE_MEAN_SQUARED_ERROR"},
        # THREE QUESTIONS, THREE ANSWERS, NOT ONE NUMBER.
        predictive_score={"metric": METRIC, "value": round(brier, 6),
                          "decomposition": dec,
                          "measures": "ACCURACY_MIXING_CALIBRATION_AND_"
                                      "DISCRIMINATION"},
        calibration=rel,
        baseline_comparison=cmp_,
        trading_profitability={
            "measured_here": False,
            "why": ("profitability is a function of acquisition cost, "
                    "per-level fees, fill probability and size -- none of "
                    "which is an input to this measurement. It is the "
                    "entry lane's question and a separate verdict"),
        },
        checks=checks,
        conditions_failed=failed,
        # IN-SAMPLE, AND INADMISSIBLE AS A BASELINE. Reported because it
        # is informative, labelled because using it was defect 2.
        in_sample_base_rate=round(sum(o for _, o in pairs) / n, 6),
        in_sample_base_rate_brier=round(
            _brier([(sum(o for _, o in pairs) / n, o) for _, o in pairs]),
            6),
        in_sample_base_rate_is=(
            "COMPUTED_FROM_THE_EVALUATED_OUTCOMES_AND_THEREFORE_NOT_AN_"
            "ADMISSIBLE_BASELINE"),
        why=("Brier %.6f (SE %.6f) over %d independent resolved fixtures "
             "against a ceiling of %.6f; held-out baseline %.4f scored "
             "%.6f, paired improvement %.6f [%.6f, %.6f]; calibration "
             "error %.6f. %s"
             % (brier, se, n, BRIER_CEILING, baseline_p,
                cmp_["baseline_brier"], cmp_["paired_mean_improvement"],
                cmp_["ci_low"], cmp_["ci_high"],
                rel["expected_calibration_error"],
                ("all four declared conditions hold" if ok else
                 "conditions not met: " + ", ".join(failed)))))
    return out


def to_row(result, *, measured_by, window_start, window_end) -> dict:
    """The `external_source_calibration` row this result justifies.

    REFUSES TO PRODUCE A PASSING ROW that the result did not earn. The
    gate reads `within_tolerance`, so this is the one place where a
    measurement becomes permission, and it will not manufacture one.
    """
    if result.get("status") not in (PASSED, FAILED):
        return {"write": False, "status": result.get("status"),
                "why": ("only a completed measurement is written. %s is "
                        "not a verdict" % result.get("status"))}
    # A VERDICT WITHOUT ITS FOUR CHECKS IS NOT THIS EVALUATOR'S VERDICT.
    if not isinstance(result.get("checks"), dict) or not result["checks"]:
        return {"write": False, "status": result.get("status"),
                "why": ("the result carries no per-condition checks, so it "
                        "was not produced by %s and cannot be written as "
                        "one of its rows" % VERSION)}
    if bool(result.get("within_tolerance")) != (
            not result.get("conditions_failed")):
        return {"write": False, "status": result.get("status"),
                "why": ("within_tolerance disagrees with the failed "
                        "conditions, so the verdict is internally "
                        "inconsistent and is not written")}
    return {
        "write": True,
        "source_version": SOURCE_VERSION,
        "sample_size": int(result["scored_events"]),
        "metric": METRIC,
        "score": float(result["score"]),
        "tolerance": float(BRIER_CEILING),
        "within_tolerance": bool(result["within_tolerance"]),
        "measured_by": measured_by,
        "window_start": window_start,
        "window_end": window_end,
        "provenance": {
            "evaluator": VERSION,
            "supersedes": SUPERSEDES,
            "scope": SCOPE,
            "acceptance": ACCEPTANCE,
            "checks": result.get("checks"),
            "conditions_failed": result.get("conditions_failed"),
            "outcome_counts": result.get("outcome_counts"),
            "unique_fixtures": result.get("unique_fixtures"),
            "payout_statements_in_scope": result.get(
                "payout_statements_in_scope"),
            "observations_collapsed": result.get("observations_collapsed"),
            "baseline": result.get("baseline"),
            "baseline_split": result.get("baseline_split"),
            "baseline_comparison": result.get("baseline_comparison"),
            "uncertainty": result.get("uncertainty"),
            "calibration": result.get("calibration"),
            "predictive_score": result.get("predictive_score"),
            "trading_profitability": result.get("trading_profitability"),
            "in_sample_base_rate": result.get("in_sample_base_rate"),
            "in_sample_base_rate_is": result.get("in_sample_base_rate_is"),
            "inputs_sha": result.get("inputs_sha"),
        },
    }


def describe() -> dict:
    return {"version": VERSION, "supersedes": SUPERSEDES, "scope": SCOPE,
            "acceptance": ACCEPTANCE,
            "outcome_classes": [RESOLVED, VOID, UNRESOLVED, UNVERIFIED],
            "verified_outcome_bases": list(VERIFIED_BASES),
            "statuses": [INSUFFICIENT, PASSED, FAILED, OUT_OF_SCOPE],
            "independent_unit": "ONE_FIXTURE",
            "point_in_time": ("the FIRST probability recorded per fixture, "
                              "never re-derived and never the best of a "
                              "source's revisions"),
            "reference_only": {
                "constant_half_brier": CONSTANT_HALF_REFERENCE,
                "what_it_is": CONSTANT_HALF_REFERENCE_IS},
            "measures_trading_profitability": False,
            "a_shortfall_is_a_result": True}


# ── THE READ AND THE RUN ─────────────────────────────────────────────

ROWS_SQL = """
    SELECT id, version, devig_method, sport_family, market, event_key,
           payout_event, probability, outcome_known, outcome,
           outcome_basis,
           extract(epoch FROM observed_at)::float8 AS observed_at_epoch,
           extract(epoch FROM decided_at)::float8  AS decided_at_epoch
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' days')::interval
     ORDER BY decided_at
"""

WRITE_SQL = """
    INSERT INTO external_source_calibration
        (source_version, measured_at, window_start, window_end,
         sample_size, metric, score, tolerance, within_tolerance,
         measured_by, provenance)
    VALUES ($1, to_timestamp($2), to_timestamp($3), to_timestamp($4),
            $5, $6, $7, $8, $9, $10, $11::jsonb)
    ON CONFLICT (source_version, measured_at) DO NOTHING
    RETURNING source_version
"""


async def measure(conn, *, experiment_id, days=90, now,
                  measured_by="SCHEDULED_CALIBRATION_RUN",
                  write=True) -> dict:
    """Run the measurement over the recorded valuations and, if it is a
    completed verdict, store it.

    A SHORTFALL IS NOT WRITTEN. `external_source_calibration` is what the
    MODEL_TRUST_DRIFT gate reads, so a row means "this was measured". An
    INSUFFICIENT result is returned to the caller and reported, and the
    gate stays shut because there is no row -- which is the same state as
    before the run, correctly.
    """
    import json as _json

    try:
        rows = [dict(r) for r in
                await conn.fetch(ROWS_SQL, experiment_id, str(int(days)))]
    except Exception as exc:                                   # noqa: BLE001
        return {"ran": False, "error": type(exc).__name__,
                "why": ("the valuation read failed, so nothing was "
                        "measured. That is not a calibration result")}
    result = evaluate(rows, measured_at=now)
    result["window_days"] = int(days)
    result["ran"] = True
    row = to_row(result, measured_by=measured_by,
                 window_start=now - float(days) * 86400.0, window_end=now)
    result["would_write"] = row["write"]
    if not (row["write"] and write):
        result["written"] = False
        result["write_refused_why"] = row.get("why") or (
            "write was not requested")
        return result
    try:
        got = await conn.fetchval(
            WRITE_SQL, row["source_version"], now, row["window_start"],
            row["window_end"], row["sample_size"], row["metric"],
            row["score"], row["tolerance"], row["within_tolerance"],
            row["measured_by"], _json.dumps(row["provenance"], default=str))
        result["written"] = got is not None
    except Exception as exc:                                   # noqa: BLE001
        result["written"] = False
        result["write_error"] = type(exc).__name__
    return result
