"""A ROW IS ELIGIBLE BECAUSE OF WHEN IT WAS OBSERVED, NOT ITS LABEL.

Owner directive 2026-09-20:

    "Verify that performance eligibility for an individual 60S markout
    depends on the ACTUAL realized observation offset satisfying the
    frozen 60S timing/tolerance contract. A row labelled '60S' must not
    become performance evidence merely because TARGET_HORIZON = 60S...
    Do not assume a label proves its realized horizon. The recorded
    timestamps are authoritative."

`shadow_markout_observability` answers a question about a HORIZON --
can a 30s markout be resolved on this capture grid at all. This module
answers a question about a ROW -- did THIS observation actually land
inside the frozen contract. They are different gates and both apply:
the horizon gate can retire a whole horizon, the row gate can retire a
single measurement while its siblings stand.

THE CONTRACT, RE-DERIVED FROM THE STORED TIMESTAMPS:

    target_at        == decision_timestamp + horizon_seconds
    |observed_at - target_at|  <=  tolerance

Both halves, because the second alone would trust `target_at`. A row
whose stored target does not reconstruct from its own decision and its
own horizon is a row whose label and whose timestamps disagree, and
the directive says the timestamps win.

THE TRAP THIS MODULE EXISTS TO AVOID, and it is a real one in this
schema. `record_markout` writes

    observed_at = m.get("observedAt") or m["targetAt"]

so a markout that observed NOTHING is stored with observed_at set to
the target instant -- a row whose target error is EXACTLY ZERO and
which, judged on timestamps alone, would read as the most perfectly
timed observation in the table. It is not an observation at all.
`l2_book_sha` is what separates them: migration 078 already requires
an OBSERVED row to name the book it was taken from, so a NULL there
means no book was ever walked. Timing is therefore judged on the
timestamps AND on the presence of the evidence, never on timestamps
alone.

WHAT THIS MODULE NEVER DOES, in the directive's own words: no
interpolation, no nearest observation outside tolerance, no zero P&L,
no deletion. An ineligible row keeps its values, keeps its place in
the ledger, and is excluded from performance. Nothing is rewritten --
every verdict here is computed when the rows are READ.
"""

from __future__ import annotations

from . import shadow_experimental_markouts as mk
from . import shadow_markout_observability as ob

# ── the verdicts ─────────────────────────────────────────────────────

WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
OUTSIDE_TOLERANCE = "OUTSIDE_TOLERANCE"
NO_OBSERVATION = "NO_OBSERVATION"
TIMING_NOT_RECORDED = "TIMING_NOT_RECORDED"
TARGET_DISAGREES_WITH_HORIZON = "TARGET_DISAGREES_WITH_HORIZON"

# HOW EXACTLY THE TARGET MUST RECONSTRUCT. This is a representation
# guard, NOT a measurement tolerance: `target_at` was computed as
# decision + timedelta(seconds=int(h)) and stored as timestamptz, so
# the only legitimate difference between it and the same sum recomputed
# in SQL is microsecond representation. One millisecond is far below
# any timing question this lane asks and far above any rounding it can
# suffer. It is never widened to admit a row; a row that misses it is
# refused.
TARGET_RECONSTRUCTION_EPSILON_MS = 1.0

HORIZON_SECONDS = dict(mk.HORIZONS)


def timing(*, horizon, decision_at=None, target_at=None, observed_at=None,
           tolerance_ms=None, l2_book_sha=None) -> dict:
    """The eight fields the directive names, for ONE markout row.

    Every one is derived from what the ledger stores. Nothing is
    inferred from the horizon label except the number of seconds it
    is supposed to mean, which is exactly what gets checked.
    """
    horizon_s = HORIZON_SECONDS.get(horizon)
    tol_s = None if tolerance_ms is None else float(tolerance_ms) / 1000.0

    realized = _seconds(observed_at, decision_at)
    error = _seconds(observed_at, target_at)
    reconstructed = _seconds(target_at, decision_at)

    status = _status(horizon_s, tol_s, error, reconstructed, l2_book_sha,
                     target_at, observed_at)

    # THE HORIZON GATE STILL APPLIES. A perfectly timed 30S row is
    # still not performance evidence, because the horizon itself
    # cannot be resolved on this capture grid. Both gates, and a row
    # needs to pass both.
    horizon_ok = horizon in ob.observable_horizons()

    return {
        "HORIZON": horizon,
        "DECISION_TIMESTAMP": decision_at,
        "TARGET_TIMESTAMP": target_at,
        "OBSERVATION_TIMESTAMP": (None if l2_book_sha is None
                                  else observed_at),
        "REALIZED_OFFSET_S": (None if l2_book_sha is None else realized),
        "TARGET_ERROR_S": (None if l2_book_sha is None else error),
        "TOLERANCE_S": tol_s,
        "TIMING_STATUS": status,
        "PERFORMANCE_ELIGIBLE": status == WITHIN_TOLERANCE and horizon_ok,
        # Said separately so a reader can tell WHICH gate refused it.
        "HORIZON_OBSERVABILITY": (ob.OBSERVABLE if horizon_ok
                                  else ob.UNOBSERVABLE),
        "why": _why(status, horizon, horizon_s, tol_s, error, reconstructed,
                    horizon_ok),
    }


def _status(horizon_s, tol_s, error, reconstructed, sha, target_at,
            observed_at):
    # 1. WAS ANYTHING ACTUALLY OBSERVED. Checked FIRST, because an
    #    unobserved row carries observed_at == target_at and would
    #    otherwise score as a flawless zero-error observation.
    if sha is None:
        return NO_OBSERVATION
    # 2. CAN THE CONTRACT EVEN BE CHECKED. A row missing its target or
    #    its tolerance cannot be verified, and an unverifiable row is
    #    not evidence. Refused rather than assumed good.
    if target_at is None or tol_s is None or observed_at is None:
        return TIMING_NOT_RECORDED
    if horizon_s is None or reconstructed is None:
        return TIMING_NOT_RECORDED
    # 3. DOES THE LABEL AGREE WITH THE CLOCK. "Do not assume a label
    #    proves its realized horizon."
    if abs(reconstructed - float(horizon_s)) * 1000.0 > \
            TARGET_RECONSTRUCTION_EPSILON_MS:
        return TARGET_DISAGREES_WITH_HORIZON
    # 4. THE FROZEN TIMING CONTRACT ITSELF.
    if error is None or abs(error) > tol_s:
        return OUTSIDE_TOLERANCE
    return WITHIN_TOLERANCE


def _why(status, horizon, horizon_s, tol_s, error, reconstructed,
         horizon_ok):
    if status == NO_OBSERVATION:
        return ("no institutional book was recorded for this row, so its "
                "observed_at is the target instant standing in for an "
                "observation that never happened -- not a zero-error "
                "measurement")
    if status == TIMING_NOT_RECORDED:
        return ("this row does not carry the timestamps and tolerance its "
                "timing would be checked against, so the contract cannot "
                "be verified and the row is not performance evidence")
    if status == TARGET_DISAGREES_WITH_HORIZON:
        return ("the stored target is %.3fs after the decision but the %s "
                "label means %ss; the label and the clock disagree and the "
                "clock is authoritative"
                % (reconstructed, horizon, horizon_s))
    if status == OUTSIDE_TOLERANCE:
        return ("the observation is %.1fs from the %s target, outside the "
                "%.0fs frozen tolerance; it is a real book at a real "
                "instant, but it is not this horizon's markout"
                % (error, horizon, tol_s))
    if not horizon_ok:
        return ("timing is within the frozen tolerance, but the %s horizon "
                "itself is unresolvable at the current capture frequency, "
                "so this row is not performance evidence" % horizon)
    return None


def _seconds(later, earlier):
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds()


# ── the same contract, as SQL ────────────────────────────────────────
#
# ONE PREDICATE, TWO CONSUMERS. The panel's money statements and the
# panel's timing read must agree about which rows are eligible, so the
# text below is the single definition both use. A second hand-written
# copy in a statement somewhere is exactly how a gate drifts.
#
# It requires `m` (the markout) and `d` (its decision) to be in scope.

_HORIZON_INTERVAL = " ".join(
    "WHEN '%s' THEN interval '%d seconds'" % (name, seconds)
    for name, seconds in mk.HORIZONS)

TARGET_RECONSTRUCTS_SQL = """
           m.target_at IS NOT NULL
       AND m.tolerance_ms IS NOT NULL
       AND abs(EXTRACT(EPOCH FROM (m.target_at - (d.decision_timestamp
               + (CASE m.horizon {cases} END))))) * 1000.0 <= {eps}
""".format(cases=_HORIZON_INTERVAL, eps=TARGET_RECONSTRUCTION_EPSILON_MS)

# THE ROW-LEVEL GATE, entire. Note what it does NOT read: it never
# consults `status`, because a status column is a conclusion someone
# else wrote and the directive says the timestamps are authoritative.
# It re-derives the verdict from the clock every time it is asked.
TIMING_ELIGIBLE_SQL = """
           m.l2_book_sha IS NOT NULL
       AND {reconstructs}
       AND abs(EXTRACT(EPOCH FROM (m.observed_at - m.target_at)))
               * 1000.0 <= m.tolerance_ms
""".format(reconstructs=TARGET_RECONSTRUCTS_SQL)


def performance_eligible_sql(horizons=None) -> str:
    """The full gate: the row's timing AND its horizon's observability."""
    names = tuple(horizons or ob.observable_horizons())
    return """
           m.horizon IN ('{names}')
       AND {timing}
    """.format(names="', '".join(sorted(names)), timing=TIMING_ELIGIBLE_SQL)


# EVERY MARKOUT WITH ITS TIMING SPELLED OUT, eligible or not. Nothing
# is filtered: a row refused by the gate appears here beside the reason
# it was refused, because a measurement that quietly vanishes from the
# panel is indistinguishable from one that was never taken.
TIMING_ROWS_SQL = """
    SELECT m.markout_id, m.experimental_decision_id, d.experiment_id,
           d.market_id, m.horizon, m.status,
           d.decision_timestamp, m.target_at, m.observed_at,
           m.tolerance_ms, m.l2_book_sha, m.l2_evidence_id,
           m.executable_markout_usd, m.mid_markout_usd,
           EXTRACT(EPOCH FROM (m.observed_at - d.decision_timestamp))
               AS realized_offset_s,
           EXTRACT(EPOCH FROM (m.observed_at - m.target_at))
               AS target_error_s,
           EXTRACT(EPOCH FROM (m.target_at - d.decision_timestamp))
               AS reconstructed_horizon_s
      FROM bettor_experimental_markouts m
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = m.experimental_decision_id
     ORDER BY m.observed_at DESC
     LIMIT $1
"""


def row_verdict(r) -> dict:
    """The directive's eight fields for one row of TIMING_ROWS_SQL."""
    out = timing(horizon=r["horizon"],
                 decision_at=r["decision_timestamp"],
                 target_at=r["target_at"],
                 observed_at=r["observed_at"],
                 tolerance_ms=r["tolerance_ms"],
                 l2_book_sha=r["l2_book_sha"])
    out["MARKOUT_ID"] = r["markout_id"]
    out["EXPERIMENTAL_DECISION_ID"] = r["experimental_decision_id"]
    out["EXPERIMENT_ID"] = r["experiment_id"]
    out["MARKET"] = r["market_id"]
    out["WRITTEN_STATUS"] = r["status"]
    out["EXECUTABLE_MARKOUT_USD"] = (
        None if r["executable_markout_usd"] is None
        else float(r["executable_markout_usd"]))
    return out


async def timing_rows(pool, *, limit=200) -> list:
    rows = await pool.fetch(TIMING_ROWS_SQL, int(limit))
    return [row_verdict(r) for r in rows]


def census(verdicts) -> dict:
    """How many rows each verdict holds, for the panel's summary tile."""
    out = {}
    for v in verdicts or ():
        key = "%s|%s" % (v["HORIZON"], v["TIMING_STATUS"])
        row = out.setdefault(key, {"horizon": v["HORIZON"],
                                   "timingStatus": v["TIMING_STATUS"],
                                   "n": 0, "performanceEligible": 0})
        row["n"] += 1
        row["performanceEligible"] += 1 if v["PERFORMANCE_ELIGIBLE"] else 0
    return out


def writer_disagreements(verdicts) -> list:
    """Rows where the WRITTEN status and the RE-DERIVED timing differ.

    Not part of the gate -- the gate ignores `status` entirely, which
    is the point. This is evidence ABOUT THE WRITER: a row stored
    OBSERVED whose timestamps fall outside the frozen tolerance means
    the write path admitted something the contract forbids, and that
    is worth seeing rather than silently correcting at read time.

    An empty list is the expected state and is itself the finding.
    """
    out = []
    for v in verdicts or ():
        observed = v.get("WRITTEN_STATUS") == "OBSERVED"
        within = v["TIMING_STATUS"] == WITHIN_TOLERANCE
        if observed == within:
            continue
        out.append({
            "markoutId": v.get("MARKOUT_ID"),
            "horizon": v["HORIZON"],
            "writtenStatus": v.get("WRITTEN_STATUS"),
            "derivedTimingStatus": v["TIMING_STATUS"],
            "targetErrorS": v["TARGET_ERROR_S"],
            "toleranceS": v["TOLERANCE_S"],
            "executableMarkoutUsd": v.get("EXECUTABLE_MARKOUT_USD"),
            "why": ("stored OBSERVED but its recorded timestamps fall "
                    "outside the frozen tolerance" if observed else
                    "not stored OBSERVED although its recorded "
                    "timestamps satisfy the frozen tolerance"),
        })
    return out
