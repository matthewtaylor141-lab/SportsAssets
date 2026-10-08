"""THE DECISION PIPELINE'S STATE DESCRIBES NOW. ITS HISTORY STAYS BESIDE IT.

WHAT PRODUCTION SAID. pm-acceptance 37836393458 (release 69a8a07e,
2026-10-08T20:10Z) read BETTOR_DECISION_PIPELINE = DEGRADED with
orphanOpportunities 75,107 (oldest 2026-09-19T18:23:33Z),
decisionWriteFailures 169 (last 2026-09-19T19:33:19Z, the V1 foreign-key
incident) and opportunityToDecisionSuccessRate 0.1911 -- while
lastSuccessfulDecision and lastOpportunityAt were the same second,
20:09:21Z. Read back an hour later (research-sql 37845454453, 21:15Z):
opportunities 93,063 and decisions 17,956, i.e. +217 and +217 since
20:09Z, and orphans STILL exactly 75,107. Every orphan carries an
annotation, and the newest annotated orphan was observed
2026-10-08T05:17:18Z: 64 s after BETTOR_EV_SHADOW_V6 froze at 05:16:14Z
(research-sql 37845463846), the tail of the V5 POLICY_CODE_DRIFT window in
which the fail-closed gate withheld every decision (423f44de). Since
then: no orphan, no write failure, decisionsWithheld 0 on every beat.

SO THE DEGRADED WAS HISTORY, AND IT COULD NEVER HAVE BEEN ANYTHING ELSE.
The state counted every orphan and every failure since migration 073 with
no window at all. Nothing -- no repair, no week of clean decisions --
could ever turn it LIVE again: the 2026-09-19 incident would have read
DEGRADED forever, which is the same failure as a tile that is always
green, inverted. An operator learns to ignore it, and the next real
outage arrives on a component everyone has stopped reading. The
heartbeat's own docstring promised DEGRADED when "a failure has been
recorded RECENTLY"; the SQL never asked when.

WHAT THIS CHANGES: the STATE is judged over ONE explicit, rolling window,
PIPELINE_WINDOW_S (24 h), and the window is named on the payload.

  - the ZERO TOLERANCE is unchanged: one orphan observed in the window,
    or one decision write failure recorded in it, is DEGRADED;
  - the ORPHAN DEFINITION is unchanged and still lives in one place, the
    migration 073 view (no decision, older than the 180 s allowance);
  - nothing is deleted, rewritten, back-filled or re-labelled, and no
    decision is created after the fact: annotations stay append-only
    notes about gaps, never decisions.

WHAT STAYS VISIBLE, LABELLED, IN THE SAME READBACK: every all-time count
(`allTime`: orphans with their oldest and newest observation, failures
with their first and last instant, the all-time rate), the orphans by the
label each was given (incident name and its observed window -- e.g.
BETTOR_WRITER_2026_09_19, 2026-09-19 19:31Z..19:33Z) and the failures by
stage and error class with their window. History informs the reader; it
no longer sets a state that claims to describe the present.

WHY 24 HOURS, AND WHY NOT AN EPOCH.
  - A day spans every cadence this pipeline has (60 s ticks, 300 s
    buckets, the universe's 2 h freshness), so a fault that recurs on any
    of them is inside the window when it is read.
  - An incident stays in the state for a full day after its last orphan:
    that is the "recently" the 2026-09-19 code promised, and long enough
    that a daily acceptance read cannot miss one.
  - "Since the running policy version froze" would be wrong in both
    directions: production's last drift orphan landed 64 s AFTER V6 froze
    (the old instance draining), so that epoch would pin DEGRADED until
    V7 for a deploy overlap, while a version bump would silently forgive
    everything before it. "Since boot" would forgive a crash-looping
    writer on every restart. A rolling window forgets nothing it has not
    held for a full day.

The rate is computed over SETTLED opportunities in the window: the ones
that have a decision plus the view's orphans. One still inside its 180 s
allowance is neither a success nor a miss yet, so it is excluded from
both sides rather than counted as a miss on every tick.

READ ONLY. Every statement is a SELECT. Nothing here is a decision input:
shadow_bettor.py (the decision path) may not read the decision ledger, and
this module is imported only by the telemetry (shadow_bettor_ops) and by
COMMAND.
"""

from __future__ import annotations

from datetime import datetime, timezone

#: The one window the pipeline's STATE is judged over (rolling, seconds).
PIPELINE_WINDOW_S = 24 * 60 * 60

#: Named on every payload, so a reader never has to infer what "now" meant.
WINDOW_BASIS = "ROLLING_24H"

WINDOW_RULE = (
    "state is judged over the last 24 h only: DEGRADED if any opportunity "
    "observed in the window passed its 180 s allowance with no decision "
    "(an orphan, per the migration 073 view) or any decision write failure "
    "was recorded in it; LIVE if every settled opportunity in the window "
    "was decided; LISTENING if nothing in the window has settled yet. "
    "Counts outside the window are kept in allTime and history and do not "
    "set the state.")

# ── the window ───────────────────────────────────────────────────────
#
# $1 the window in seconds, $2 the lane. ONE scan of the orphan view (its
# definition is not repeated here), one indexed scan of the window's
# opportunities, and the failures by their own time index.
WINDOW_SQL = """
    WITH bound AS (
        SELECT now() AS until, now() - make_interval(secs => $1) AS since),
    orph AS (
        SELECT count(*)         AS n,
               min(v.observed_at) AS oldest,
               max(v.observed_at) AS newest
          FROM bettor_orphan_opportunities v, bound
         WHERE v.observed_at >= bound.since),
    opp AS (
        SELECT count(*) AS n,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM shadow_decisions d
                    WHERE d.bettor_opportunity_id = o.bettor_opportunity_id))
                   AS decided
          FROM bettor_opportunities o, bound
         WHERE o.observed_at >= bound.since),
    fail AS (
        SELECT count(*) AS n, max(f.failed_at) AS last
          FROM bettor_decision_failures f, bound
         WHERE f.failed_at >= bound.since)
    SELECT bound.since, bound.until,
           opp.n          AS opportunities,
           opp.decided    AS decided,
           (SELECT count(*) FROM shadow_decisions d
             WHERE d.lane = $2 AND d.decision_ts >= bound.since)
                          AS decisions,
           orph.n         AS orphans,
           orph.oldest    AS oldest_orphan,
           orph.newest    AS newest_orphan,
           fail.n         AS failures,
           fail.last      AS last_failure
      FROM bound, orph, opp, fail
"""

# ── all time: every count the old state was computed from, unchanged ──
ALL_TIME_SQL = """
    WITH orph AS (
        SELECT count(*)           AS n,
               min(observed_at)   AS oldest,
               max(observed_at)   AS newest
          FROM bettor_orphan_opportunities),
    fail AS (
        SELECT count(*) AS n, min(failed_at) AS first, max(failed_at) AS last
          FROM bettor_decision_failures)
    SELECT
      (SELECT count(*) FROM bettor_opportunities)          AS opportunities,
      (SELECT max(observed_at) FROM bettor_opportunities)  AS last_opportunity,
      (SELECT count(*) FROM shadow_decisions
        WHERE lane = $1)                                    AS decisions,
      (SELECT max(created_at) FROM shadow_decisions
        WHERE lane = $1)                                    AS last_decision,
      orph.n AS orphans, orph.oldest AS oldest_orphan,
      orph.newest AS newest_orphan,
      fail.n AS failures, fail.first AS first_failure,
      fail.last AS last_failure,
      (SELECT error_text FROM bettor_decision_failures
        ORDER BY failed_at DESC LIMIT 1)                    AS last_failure_text
      FROM orph, fail
"""

# ── history, by the label each gap was given at the time ─────────────
#
# The annotation table is append-only and its rows are notes ABOUT gaps
# (migration 073): grouping them never turns one into a decision.
LABELS_SQL = """
    SELECT annotation_kind, incident,
           count(DISTINCT bettor_opportunity_id) AS opportunities,
           min(observed_at)  AS first_observed,
           max(observed_at)  AS last_observed,
           min(annotated_at) AS first_annotated,
           max(annotated_at) AS last_annotated
      FROM bettor_opportunity_annotations
     GROUP BY annotation_kind, incident
     ORDER BY min(observed_at), annotation_kind, incident
"""

FAILURES_SQL = """
    SELECT stage, error_class, count(*) AS failures,
           min(failed_at) AS first_at, max(failed_at) AS last_at
      FROM bettor_decision_failures
     GROUP BY stage, error_class
     ORDER BY min(failed_at), stage, error_class
"""


def _iso(v):
    """Applied field by field, to the timestamp columns named at each call
    site, and only a datetime is converted: any other value passes through
    so an unexpected type still fails loudly wherever it is serialized
    strictly (the 62-silent-heartbeats lesson, shadow_bettor_ops.
    TIMESTAMP_FIELDS). Never a blanket default=str."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    return v


def _int(v) -> int:
    return int(v or 0)


def state_of(window: dict) -> str:
    """The pipeline's state from `read_window()`'s block. Pure.

    Zero tolerance, exactly as before -- only the population changed: one
    orphan or one recorded failure in the window is DEGRADED. LIVE needs a
    decided opportunity in the window, because "nothing has failed" is not
    the same claim as "decisions are landing". COMMAND states the same rule
    inline (api/command_shadow._pipeline, where a test pins its shape); a
    real-database test holds the two to the same answer."""
    if (_int(window.get("orphanOpportunities"))
            or _int(window.get("decisionWriteFailures"))):
        return "DEGRADED"
    if _int(window.get("opportunitiesDecided")):
        return "LIVE"
    return "LISTENING"


def settled_rate(decided, orphans):
    """decided / (decided + orphans) over the window, or None when nothing
    has settled. No target is declared (owner, 2026-09-19): the number is
    reported and judged by a human."""
    settled = _int(decided) + _int(orphans)
    return None if not settled else round(_int(decided) / settled, 4)


async def read_window(pool, *, lane: str,
                      window_s: float = PIPELINE_WINDOW_S) -> dict:
    """The WINDOW block alone, JSON-ready: what the state is judged on.
    The heartbeat reads only this, every tick; it already carries its own
    all-time counts. Raises on a failed read."""
    w = dict(await pool.fetchrow(WINDOW_SQL, float(window_s), lane))
    return {
        "basis": WINDOW_BASIS,
        "seconds": int(window_s),
        "since": _iso(w["since"]),
        "until": _iso(w["until"]),
        "rule": WINDOW_RULE,
        "opportunitiesObserved": _int(w["opportunities"]),
        "opportunitiesDecided": _int(w["decided"]),
        "decisionsRecorded": _int(w["decisions"]),
        "orphanOpportunities": _int(w["orphans"]),
        "oldestOrphanAt": _iso(w["oldest_orphan"]),
        "newestOrphanAt": _iso(w["newest_orphan"]),
        "decisionWriteFailures": _int(w["failures"]),
        "lastFailure": _iso(w["last_failure"]),
        "opportunityToDecisionSuccessRate": settled_rate(w["decided"],
                                                         w["orphans"]),
    }


async def read(pool, *, lane: str,
               window_s: float = PIPELINE_WINDOW_S) -> dict:
    """{window, allTime, history} from rows, JSON-ready. Raises on a failed
    read: the callers already turn that into their own named state
    (STORE_NOT_READY in COMMAND, NOT_IDENTIFIED in the heartbeat)."""
    window = await read_window(pool, lane=lane, window_s=window_s)
    a = dict(await pool.fetchrow(ALL_TIME_SQL, lane))
    labels = await pool.fetch(LABELS_SQL)
    fails = await pool.fetch(FAILURES_SQL)
    all_opps = _int(a["opportunities"])
    all_time = {
        "opportunitiesObserved": all_opps,
        "decisionsRecorded": _int(a["decisions"]),
        "orphanOpportunities": _int(a["orphans"]),
        "oldestOrphanAt": _iso(a["oldest_orphan"]),
        "newestOrphanAt": _iso(a["newest_orphan"]),
        "decisionWriteFailures": _int(a["failures"]),
        "firstFailure": _iso(a["first_failure"]),
        "lastFailure": _iso(a["last_failure"]),
        # THE OLD NUMBER, BY ITS OLD FORMULA, so the 0.1911 production
        # showed can still be read and compared.
        "opportunityToDecisionSuccessRate": (
            None if not all_opps
            else round(_int(a["decisions"]) / all_opps, 4)),
        "affectsState": False,
    }
    history = {
        "orphanLabels": [
            {"annotationKind": r["annotation_kind"],
             "incident": r["incident"],
             "opportunities": _int(r["opportunities"]),
             "firstObservedAt": _iso(r["first_observed"]),
             "lastObservedAt": _iso(r["last_observed"]),
             "firstAnnotatedAt": _iso(r["first_annotated"]),
             "lastAnnotatedAt": _iso(r["last_annotated"])}
            for r in labels],
        "failures": [
            {"stage": r["stage"], "errorClass": r["error_class"],
             "failures": _int(r["failures"]),
             "firstAt": _iso(r["first_at"]), "lastAt": _iso(r["last_at"])}
            for r in fails],
        "affectsState": False,
        "why": ("kept and shown, never deleted or re-labelled; history "
                "outside the window does not set a state that describes "
                "the pipeline now"),
    }
    return {"window": window, "allTime": all_time, "history": history,
            "lastSuccessfulDecision": _iso(a["last_decision"]),
            "lastOpportunityAt": _iso(a["last_opportunity"]),
            "lastFailureText": a["last_failure_text"]}


def detail(got: dict) -> str:
    """One sentence an operator can read without opening the payload: the
    window that set the state, then the all-time history that did not."""
    w, a = got["window"], got["allTime"]
    head = ("every opportunity must end in a decision or a named failure; "
            "an orphan is an opportunity past its 180s allowance with "
            "neither. ")
    now = ("STATE = the last %dh (since %s): %d orphans, %d write failures, "
           "%d of %d settled opportunities decided. "
           % (w["seconds"] // 3600, w["since"], w["orphanOpportunities"],
              w["decisionWriteFailures"], w["opportunitiesDecided"],
              w["opportunitiesDecided"] + w["orphanOpportunities"]))
    past = ("ALL TIME, kept in allTime and history and not part of the "
            "state: %d orphans (observed %s .. %s) and %d write failures "
            "(%s .. %s)."
            % (a["orphanOpportunities"], a["oldestOrphanAt"] or "none",
               a["newestOrphanAt"] or "none", a["decisionWriteFailures"],
               a["firstFailure"] or "none", a["lastFailure"] or "none"))
    return head + now + past
