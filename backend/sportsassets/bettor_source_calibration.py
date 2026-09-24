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
EVERYTHING BELOW IS DECLARED BEFORE THE DATA IS LOOKED AT, and that is
the point of writing it here rather than choosing it in a report:

  SCOPE            which source version, which method, which sport
                   families and which market. A score computed over a
                   different scope is a different number.
  POINT IN TIME    the probability used is the one RECORDED at decision
                   time, never re-derived, and per unique event it is the
                   FIRST such record. Taking the last, or the best, or an
                   average would be choosing among a source's own
                   revisions after seeing which way the event went.
  UNIQUE EVENT     one observation per (event, payout event). A source
                   quoted every fifteen minutes for six hours produces
                   twenty-four rows about one coin flip, and scoring all
                   of them would report a sample twenty-four times larger
                   than the evidence.
  RESOLVED / VOID / UNRESOLVED
                   only RESOLVED events are scored. VOID and UNRESOLVED
                   are counted and excluded, and their counts are part of
                   the result: a measurement that silently dropped them
                   would hide how much of the record it could not use.
  METRIC           BRIER, the mean squared error of the stated
                   probability against the 0/1 outcome. Chosen because it
                   is a proper score: it cannot be improved by shading a
                   probability away from the honest one.
  ACCEPTANCE       two conditions, BOTH required -- a minimum sample and
                   a Brier no worse than the declared ceiling.
  REPRODUCIBLE     the result carries the inputs' own hash, so the same
                   rows produce the same answer and a different answer
                   means different rows.

A SHORTFALL IS A RESULT. With too few resolved events the answer is
INSUFFICIENT, with the number still needed stated, and the gate stays
shut. That is the expected first answer and it is not a failure of the
measurement.
"""

from __future__ import annotations

import hashlib
import json

from . import bettor_pinnacle_devig as devig

VERSION = "EXTERNAL_SOURCE_CALIBRATION_V1"

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
    "why_scope_matters": (
        "a Brier score over a different set of sports, markets or de-vig "
        "methods is a different number about a different thing. The gate "
        "reads one row, so the row has to say what it covers"),
}

# ── THE PREDECLARED METRIC AND CRITERION ─────────────────────────────
METRIC = "BRIER"

#: The ceiling. A Brier at or below this passes; above it fails.
#:
#: WHERE THE NUMBER COMES FROM, so it is not a taste. The base rate of a
#: two-outcome sports market sits near 0.5, and a forecaster that always
#: said 0.5 would score 0.25 -- that is the do-nothing benchmark, and a
#: source no better than it is worth nothing to us. 0.24 requires a
#: measurable improvement on it while staying far from the 0.20-0.21 range
#: a genuinely sharp book is expected to reach, so it is a floor on
#: usefulness rather than a claim about excellence.
BRIER_CEILING = 0.24

#: Chosen from what the ceiling has to be able to distinguish rather than
#: from convenience: separating a Brier of 0.24 from the 0.25 do-nothing
#: benchmark needs a few hundred independent events before sampling noise
#: stops dominating. 300 is that order of magnitude, declared in advance,
#: and the gate stays shut until it is reached however good the early
#: numbers look.
MIN_RESOLVED_EVENTS = 300

ACCEPTANCE = {
    "metric": METRIC,
    "ceiling": BRIER_CEILING,
    "min_resolved_events": MIN_RESOLVED_EVENTS,
    "both_required": True,
    "reference_no_skill_brier": 0.25,
    "why": ("a proper score below a stated ceiling on a stated minimum "
            "sample. Either alone is satisfiable by luck or by a tiny "
            "favourable subset"),
    "declared_before_any_data_was_read": True,
}

# ── outcome classes ──────────────────────────────────────────────────
RESOLVED = "RESOLVED"
VOID = "VOID"
UNRESOLVED = "UNRESOLVED"

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
    """RESOLVED, VOID or UNRESOLVED for one valuation row.

    `outcome` is 1 when the event the contract PAYS ON occurred, 0 when it
    did not, and the join writes it only when the venue's own answer was
    readable. A row marked known with an outcome that is neither is VOID:
    the market returned stakes rather than paying a side, and a voided
    event has no 0/1 truth to score a probability against.
    """
    if not row.get("outcome_known"):
        return UNRESOLVED
    o = row.get("outcome")
    if o is None:
        return VOID
    try:
        o = int(o)
    except (TypeError, ValueError):
        return VOID
    return RESOLVED if o in (0, 1) else VOID


def unique_events(rows) -> dict:
    """One observation per unique event, and it is the FIRST one.

    Keyed on (event_key, payout_event) -- the same fixture priced on two
    different payout events is two statements, and both are scoreable.
    Earliest `observed_at` wins; ties break on the row id so the choice is
    deterministic and does not depend on row order.
    """
    best: dict = {}
    for r in rows:
        key = (str(r.get("event_key") or ""),
               str(r.get("payout_event") or ""))
        if not key[0]:
            continue
        cur = best.get(key)
        mine = (float(r.get("observed_at_epoch") or 0.0),
                int(r.get("id") or 0))
        if cur is None or mine < cur[0]:
            best[key] = (mine, r)
    return {k: v[1] for k, v in best.items()}


def evaluate(rows, *, measured_at=None) -> dict:
    """The calibration measurement. Pure: it reads rows and nothing else.

    `rows` are `external_valuations` records carrying at least: id,
    version, devig_method, sport_family, market, event_key, payout_event,
    probability, observed_at_epoch, outcome_known, outcome.
    """
    rows = list(rows or [])
    scoped = [r for r in rows if in_scope(r)]
    out = {
        "version": VERSION,
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

    picked = unique_events(scoped)
    out["unique_events"] = len(picked)
    out["observations_collapsed"] = len(scoped) - len(picked)

    counts = {RESOLVED: 0, VOID: 0, UNRESOLVED: 0}
    scored = []
    for r in picked.values():
        cls = classify(r)
        counts[cls] += 1
        if cls != RESOLVED:
            continue
        p = r.get("probability")
        if p is None:
            # A RESOLVED EVENT WITH NO STATED PROBABILITY is not scoreable
            # and is not a zero. It moves to UNRESOLVED-for-scoring and is
            # counted, because pretending p=0 would punish the source for
            # a row it never filled in.
            counts[RESOLVED] -= 1
            counts[UNRESOLVED] += 1
            continue
        scored.append((float(p), int(r["outcome"])))

    out["outcome_counts"] = dict(counts)
    out["scored_events"] = len(scored)
    # THE INPUTS' OWN HASH, so the same rows give the same answer and a
    # different answer means the rows changed.
    out["inputs_sha"] = _sha(sorted(
        (str(k[0]), str(k[1]), r.get("probability"), r.get("outcome"),
         r.get("outcome_known")) for k, r in picked.items()))

    n = len(scored)
    if n < MIN_RESOLVED_EVENTS:
        out.update(
            status=INSUFFICIENT, within_tolerance=False,
            score=(None if not n else
                   round(sum((p - o) ** 2 for p, o in scored) / n, 6)),
            shortfall_events=MIN_RESOLVED_EVENTS - n,
            why=("%d resolved, scoreable events inside the declared scope; "
                 "%d are required before this metric can separate %.2f "
                 "from the %.2f no-skill benchmark. Collection continues "
                 "and the gate stays shut"
                 % (n, MIN_RESOLVED_EVENTS, BRIER_CEILING, 0.25)))
        # A PROVISIONAL SCORE IS REPORTED AND IS NOT AN ANSWER. Naming it
        # provisional is safer than withholding it: it lets a reader watch
        # collection without letting anything treat it as a verdict.
        out["score_is_provisional"] = True
        return out

    brier = sum((p - o) ** 2 for p, o in scored) / n
    # THE NO-SKILL COMPARISON, on the same events. A Brier is only
    # interpretable against the base rate it was measured on.
    base = sum(o for _, o in scored) / n
    base_brier = sum((base - o) ** 2 for _, o in scored) / n
    ok = brier <= BRIER_CEILING
    out.update(
        status=(PASSED if ok else FAILED),
        within_tolerance=bool(ok),
        score=round(brier, 6),
        score_is_provisional=False,
        base_rate=round(base, 6),
        base_rate_brier=round(base_brier, 6),
        beats_base_rate=bool(brier < base_brier),
        why=("Brier %.6f over %d resolved unique events against a ceiling "
             "of %.6f (base-rate Brier %.6f on the same events)"
             % (brier, n, BRIER_CEILING, base_brier)))
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
            "scope": SCOPE,
            "acceptance": ACCEPTANCE,
            "outcome_counts": result.get("outcome_counts"),
            "unique_events": result.get("unique_events"),
            "observations_collapsed": result.get("observations_collapsed"),
            "base_rate": result.get("base_rate"),
            "base_rate_brier": result.get("base_rate_brier"),
            "inputs_sha": result.get("inputs_sha"),
        },
    }


def describe() -> dict:
    return {"version": VERSION, "scope": SCOPE, "acceptance": ACCEPTANCE,
            "outcome_classes": [RESOLVED, VOID, UNRESOLVED],
            "statuses": [INSUFFICIENT, PASSED, FAILED, OUT_OF_SCOPE],
            "point_in_time": ("the FIRST probability recorded per unique "
                              "event, never re-derived and never the best "
                              "of a source's revisions"),
            "a_shortfall_is_a_result": True}


# ── THE READ AND THE RUN ─────────────────────────────────────────────

ROWS_SQL = """
    SELECT id, version, devig_method, sport_family, market, event_key,
           payout_event, probability, outcome_known, outcome,
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
