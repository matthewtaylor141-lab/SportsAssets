"""A LATENCY NUMBER REQUIRES ONE CLOCK, AND THESE ROWS HAVE TWO.

Owner directive 2026-09-20 S6:

    "The current MODELED_EXECUTION_LATENCY_MS must not appear in
    COMMAND as an observed or valid latency statistic... means the
    timestamps come from incompatible clock semantics... If clocks are
    incomparable, say LATENCY_STATUS = NOT_IDENTIFIED_CLOCK_DOMAIN_
    CONFLICT. Do not manufacture a latency number."

WHAT PRODUCTION ACTUALLY SHOWS on the first X1 trade:

    DECISION        01:56:43.514
    FEATURES_SEALED 01:56:46.206     (+2.7s AFTER the decision)
    BETTOR_RECEIVED 01:56:49.228     (+5.7s AFTER the decision)
    MODEL_START/END 01:56:49.755     (+6.2s AFTER the decision)
    MODELED_ARRIVAL 01:56:50.005

The decision instant precedes the model run that produced it. That is
not a slow pipeline, it is two clocks: DECISION_COMMIT comes from the
tick's sealed, reproducible clock, while MODEL_START/END and
BETTOR_RECEIVED come from wall-clock at execution time. Subtracting
one from the other yields a number with no physical meaning.

AND THE NUMBER IT YIELDED WAS BEING PUBLISHED.
MODELED_EXECUTION_LATENCY_MS was computed as
(modeled_arrival - decision) and came out at 4,390-9,176 ms across the
four X1 rows. The only genuinely modeled component is

    MODELED_ARRIVAL - MODEL_END == 250.0 ms  exactly, on every row

which is the frozen ARRIVAL_LATENCY_MS constant. Everything else in
that figure is the gap between two incomparable clocks.

SO: THE ORDER IS CHECKED, AND WHEN IT FAILS NOTHING IS REPORTED. No
repair, no absolute value, no "probably about 250ms". The verdict is
NOT_IDENTIFIED_CLOCK_DOMAIN_CONFLICT and the offending pair is named,
because a reader who is told a number is unavailable can go and fix
the clocks, while a reader given a plausible number cannot.

OLD ROWS ARE NOT REPAIRED. "Do not silently repair old rows." The
check runs when the rows are READ; every stored instant stays exactly
as written.
"""

from __future__ import annotations

# THE LIFECYCLE, in the order the physical process requires. Named as
# S6 names them, so the contract is legible beside the directive.
LIFECYCLE = (
    ("SOURCE_EVENT_TIME", "venue_source_timestamp"),
    ("BETTOR_RECEIVED_TIME", "bettor_received_timestamp"),
    ("FEATURE_SEAL_TIME", "features_sealed_timestamp"),
    ("MODEL_START_TIME", "model_start_timestamp"),
    ("MODEL_END_TIME", "model_end_timestamp"),
    ("DECISION_COMMIT_TIME", "decision_timestamp"),
    ("MODELED_ARRIVAL_TIME", "modeled_arrival_timestamp"),
)

OK = "MONOTONIC"
CONFLICT = "NOT_IDENTIFIED_CLOCK_DOMAIN_CONFLICT"
INCOMPLETE = "NOT_IDENTIFIED_INCOMPLETE_LIFECYCLE"

# The one interval that IS a modeled quantity rather than a measured
# one, and is therefore reportable on its own: the frozen arrival
# latency added to the model's end instant.
MODELED_COMPONENT = ("MODEL_END_TIME", "MODELED_ARRIVAL_TIME")


def lifecycle(row) -> dict:
    """The seven instants, in order, with their pairwise deltas."""
    out = []
    for name, column in LIFECYCLE:
        out.append({"name": name, "column": column,
                    "at": row.get(column)})
    return out


def integrity(row) -> dict:
    """Is this row's latency usable, and if not, exactly where it breaks.

    Returns a verdict either way. A row whose clocks disagree is a real
    finding about the pipeline, not missing data.
    """
    stages = lifecycle(row)
    present = [s for s in stages if s["at"] is not None]
    missing = [s["name"] for s in stages if s["at"] is None]

    # Comparable instants only. SOURCE_EVENT_TIME is the venue's own
    # string and is left out of the ordering test rather than parsed
    # into a clock it may not share -- naming it as not-compared is
    # more honest than coercing it.
    ordered = [s for s in present if s["name"] != "SOURCE_EVENT_TIME"]

    violations = []
    for earlier, later in zip(ordered, ordered[1:]):
        if later["at"] < earlier["at"]:
            violations.append({
                "expectedEarlier": earlier["name"],
                "expectedLater": later["name"],
                "byMs": round(
                    (earlier["at"] - later["at"]).total_seconds() * 1000.0,
                    1),
            })

    if violations:
        status = CONFLICT
    elif len(ordered) < 2:
        status = INCOMPLETE
    else:
        status = OK

    return {
        "LATENCY_STATUS": status,
        "violations": violations,
        "missingStages": missing,
        "notCompared": ["SOURCE_EVENT_TIME"],
        # S6: usable ONLY when the ordering the semantics require holds.
        "latencyUsableEconomically": status == OK,
        "MODELED_EXECUTION_LATENCY_MS": (
            _f(row.get("modeled_execution_latency_ms")) if status == OK
            else None),
        "MODELED_EXECUTION_LATENCY_STATUS": (
            "OBSERVED_ORDERING_VERIFIED" if status == OK else status),
        # The one interval that survives a clock conflict, because both
        # of its endpoints come from the same clock.
        "MODELED_ARRIVAL_MINUS_MODEL_END_MS": _delta_ms(
            row, "model_end_timestamp", "modeled_arrival_timestamp"),
        "modeledComponentNote": (
            "MODELED_ARRIVAL - MODEL_END is a configured constant added "
            "to the model's own end instant, not an observed execution "
            "latency; both endpoints come from one clock, so it remains "
            "readable even when the lifecycle ordering fails"),
        "why": (
            "the recorded instants are not monotonic, so at least two "
            "of them come from different clock domains and their "
            "difference has no physical meaning; no latency number is "
            "reported for this row" if status == CONFLICT else
            "too few comparable instants to establish an ordering"
            if status == INCOMPLETE else None),
    }


def _f(v):
    return None if v is None else float(v)


def _delta_ms(row, a, b):
    x, y = row.get(a), row.get(b)
    if x is None or y is None:
        return None
    return round((y - x).total_seconds() * 1000.0, 1)


# ── §8: components, never one number ─────────────────────────────────
#
# "Do not publish one 'latency' number unless its components are
# legitimate." Each component is reported on its own and stands or
# falls on its own two endpoints, so a broken pair does not silence a
# sound one.

CLOCK_ORDER_INVALID = "CLOCK_ORDER_INVALID"

MODELED_ASSUMPTION_MS = 250
MODELED_ASSUMPTION_LABEL = "MODELED_EXECUTION_LATENCY_ASSUMPTION"
OBSERVED_LABEL = "OBSERVED_EXECUTION_LATENCY"

COMPONENTS = (
    ("SOURCE_TO_RECEIPT_LATENCY", "venue_source_timestamp",
     "bettor_received_timestamp"),
    ("FEATURE_PROCESSING_LATENCY", "bettor_received_timestamp",
     "features_sealed_timestamp"),
    ("MODEL_COMPUTE_LATENCY", "model_start_timestamp",
     "model_end_timestamp"),
    ("DECISION_TO_MODELED_ARRIVAL_LATENCY", "decision_timestamp",
     "modeled_arrival_timestamp"),
)


def components(row) -> dict:
    """Each interval on its own, with the status of its OWN endpoints.

    SOURCE_TO_RECEIPT is always NOT_IDENTIFIED today: the venue's
    source timestamp is a text field in a clock domain we have not
    established, and parsing it into a comparable instant would be the
    manufacturing §6 forbids.
    """
    out = {}
    for name, a, b in COMPONENTS:
        x, y = row.get(a), row.get(b)
        if isinstance(x, str) or isinstance(y, str):
            out[name] = {"ms": None, "status": "NOT_IDENTIFIED_CLOCK_DOMAIN",
                         "why": ("one endpoint is the venue's own string "
                                 "in an unestablished clock domain")}
            continue
        if x is None or y is None:
            out[name] = {"ms": None, "status": "NOT_RECORDED"}
            continue
        ms = round((y - x).total_seconds() * 1000.0, 1)
        out[name] = {
            "ms": ms if ms >= 0 else None,
            # A NEGATIVE INTERVAL IS NOT A FAST ONE. It means the two
            # endpoints do not share a clock, and the component is
            # withheld rather than shown as a negative latency.
            "status": ("OBSERVED" if ms >= 0 else CLOCK_ORDER_INVALID),
            "why": (None if ms >= 0 else
                    "%s is %.1fms AFTER %s; the endpoints do not share "
                    "a clock" % (a, -ms, b)),
        }

    # §8: the one modelled quantity, labelled as an ASSUMPTION and
    # never as an observation.
    out[MODELED_ASSUMPTION_LABEL] = {
        "ms": MODELED_ASSUMPTION_MS,
        "status": "CONFIGURED_CONSTANT",
        "measured": _delta_ms(row, "model_end_timestamp",
                              "modeled_arrival_timestamp"),
        "why": ("the frozen arrival latency added to the model's end "
                "instant; both endpoints share one clock, so it is "
                "readable even when the lifecycle ordering fails -- but "
                "it is an assumption, not a measurement"),
    }
    out[OBSERVED_LABEL] = {
        "ms": None,
        "status": "NOT_IDENTIFIED",
        "why": ("no order is ever submitted in this lane, so no "
                "execution latency has ever been observed"),
    }
    return out


def census(verdicts) -> dict:
    """How many decisions carry a usable latency, and how many do not."""
    out = {OK: 0, CONFLICT: 0, INCOMPLETE: 0}
    pairs = {}
    for v in verdicts or ():
        out[v["LATENCY_STATUS"]] = out.get(v["LATENCY_STATUS"], 0) + 1
        for bad in v["violations"]:
            key = "%s>%s" % (bad["expectedEarlier"], bad["expectedLater"])
            pairs[key] = pairs.get(key, 0) + 1
    return {"byStatus": out, "violatingPairs": pairs,
            "usableForEconomics": out.get(OK, 0)}


LATENCY_ROWS_SQL = """
    SELECT d.experimental_decision_id, d.experiment_id,
           d.venue_source_timestamp, d.bettor_received_timestamp,
           d.features_sealed_timestamp, d.model_start_timestamp,
           d.model_end_timestamp, d.decision_timestamp,
           d.modeled_arrival_timestamp,
           d.modeled_execution_latency_ms, d.source_to_decision_ms,
           d.market_data_lag_ms, d.model_compute_ms, d.latency_regime
      FROM bettor_experimental_decisions d
     WHERE d.position_id IS NOT NULL
     ORDER BY d.decision_timestamp DESC
     LIMIT $1
"""


async def latency_rows(pool, *, limit=200) -> list:
    rows = await pool.fetch(LATENCY_ROWS_SQL, int(limit))
    out = []
    for r in rows:
        v = integrity(r)
        v["EXPERIMENTAL_DECISION_ID"] = r["experimental_decision_id"]
        v["EXPERIMENT_ID"] = r["experiment_id"]
        v["LATENCY_REGIME"] = r["latency_regime"]
        v["components"] = components(r)
        out.append(v)
    return out
