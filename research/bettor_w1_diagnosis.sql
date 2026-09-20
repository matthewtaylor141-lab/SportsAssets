-- W1 FOLLOW-UP STARVATION: THE DIAGNOSIS, BEFORE ANY REPAIR.
--
-- Owner 2026-09-20: "Before changing ORDER BY, establish the actual
-- bottleneck."
--
-- TWO CANDIDATE CAUSES, AND THIS QUERY SEPARATES THEM.
--
--   H1 ORDERING   mids_due sorts oldest-first, so reads are dispatched
--                 at the far edge of their window.
--   H2 STARVATION follow_budget = min(MAX_FOLLOWUP_READS, leftover
--                 budget from the sampling pass). When the sampling
--                 pass exhausts its budget, follow_budget is 0 and the
--                 follow-up loop breaks immediately, having attempted
--                 nothing at all.
--
-- H2 predicts that ticks with obs_skipped_budget > 0 have
-- fu_attempted = 0, because the sampling pass left nothing. H1 predicts
-- no such relationship -- reads would still be attempted, just late.
-- Section C decides between them.
--
-- AND A MEASUREMENT DEFECT THIS QUERY EXISTS TO WORK AROUND.
-- The worker computes fu_due AFTER applying the budget as a LIMIT:
--     due = mids_due(horizon, limit=follow_budget)
--     stats["fuDue"] += len(due)
-- so fu_due can never exceed the budget, and FU_DUE == FU_ATTEMPTED is
-- a tautology rather than a reconciliation. TRUE DEMAND HAS NEVER BEEN
-- MEASURED. Section B measures it here, from the observations table,
-- with no limit applied.
--
-- Read only. No settlement term.

-- ── A. NOT_YET_DUE vs DUE-BUT-PENDING vs MISSING, all 66 accounted ──
-- Every cohort observation lands in exactly one bucket per horizon.
SELECT 'A_ACCOUNTING_' || lpad(h.horizon::text, 4, '0') AS section,
       k, v
  FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
 CROSS JOIN LATERAL (
    SELECT 'COHORT_TOTAL' AS k, count(*)::text AS v
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
    UNION ALL
    -- NOT_YET_DUE: the horizon itself has not elapsed.
    SELECT 'NOT_YET_DUE', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND o.observed_at >= now() - (h.horizon || ' seconds')::interval
    UNION ALL
    SELECT 'DUE_ON_TIME', count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND m.timing_class = 'ON_TIME'
    UNION ALL
    SELECT 'DUE_LATE_RECOVERY', count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND m.timing_class = 'LATE_RECOVERY'
    UNION ALL
    SELECT 'DUE_PENDING_IN_WINDOW', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND o.observed_at <  now() - (h.horizon || ' seconds')::interval
       AND o.observed_at >= now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
    UNION ALL
    SELECT 'DUE_FINALLY_MISSING', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND o.observed_at <  now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
 ) x

UNION ALL

-- ── B. TRUE DEMAND, measured without the budget limit ───────────────
-- What mids_due WOULD have returned each time, had it not been capped.
-- This is the denominator the worker has never recorded.
SELECT 'B_TRUE_DEMAND',
       h.horizon::text || 's_OUTSTANDING_ELIGIBLE_NOW',
       count(*)::text
  FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
 CROSS JOIN bettor_state_observations o
 WHERE o.observed_at <  now() - (h.horizon || ' seconds')::interval
   AND o.observed_at >= now() - ((h.horizon + 600) || ' seconds')::interval
   AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                    WHERE m.observation_id = o.observation_id
                      AND m.horizon_s = h.horizon)
 GROUP BY 2

UNION ALL

-- ── C. THE DECIDING TEST: does a starved sampling pass zero the ─────
-- follow-up budget? H2 predicts fu_attempted = 0 on every tick whose
-- sampling pass ran out of budget.
SELECT 'C_STARVATION_TEST',
       CASE WHEN obs_skipped_budget > 0
            THEN 'TICKS_WHERE_SAMPLING_EXHAUSTED_BUDGET'
            ELSE 'TICKS_WITH_BUDGET_LEFT_OVER' END,
       count(*)::text || ' ticks, fu_attempted total='
       || sum(fu_attempted)::text
       || ', fu_attempted max=' || max(fu_attempted)::text
       || ', obs_attempted total=' || sum(obs_attempted)::text
  FROM bettor_capture_ticks
 WHERE tick_at >= '2026-09-20T21:28:19Z'::timestamptz
   AND tick_at <  '2026-09-20T21:58:19Z'::timestamptz
 GROUP BY 2

UNION ALL

-- ── D. PER-TICK TRACE, every W1 tick in order ──────────────────────
SELECT 'D_TICK_TRACE',
       to_char(tick_at, 'HH24:MI:SS'),
       'sched=' || obs_scheduled::text
       || ' att=' || obs_attempted::text
       || ' skipB=' || obs_skipped_budget::text
       || ' rl=' || obs_rate_limited::text
       || ' pace=' || pacing_s
       || ' | fu_due=' || fu_due::text
       || ' fu_att=' || fu_attempted::text
       || ' fu_on=' || fu_on_time::text
       || ' fu_late=' || fu_late::text
  FROM bettor_capture_ticks
 WHERE tick_at >= '2026-09-20T21:28:19Z'::timestamptz
   AND tick_at <  '2026-09-20T21:58:19Z'::timestamptz

UNION ALL

-- ── E. OBSERVATION TRACE: due time vs read time, per read ──────────
-- Shows whether a served read was dispatched near its horizon or near
-- its expiry. Includes reads whose lag exceeds horizon+600, which can
-- only happen if selection occurred before expiry and completion after.
SELECT 'E_OBS_TRACE',
       to_char(o.observed_at, 'HH24:MI:SS') || ' h=' || m.horizon_s::text,
       'due=' || to_char(o.observed_at
                 + (m.horizon_s || ' seconds')::interval, 'HH24:MI:SS')
       || ' read=' || to_char(m.read_at, 'HH24:MI:SS')
       || ' lag=' || m.actual_lag_s
       || ' cls=' || coalesce(m.timing_class, '-')
       || CASE WHEN m.actual_lag_s::numeric > m.horizon_s + 600
               THEN ' PAST_EXPIRY_SELECTED_BEFORE_COMPLETED_AFTER'
               ELSE '' END
  FROM bettor_state_observations o
  JOIN bettor_state_mids m ON m.observation_id = o.observation_id
 WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
   AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
   AND m.actual_lag_s IS NOT NULL
   AND m.actual_lag_s <> 'NOT_IDENTIFIED'

UNION ALL

-- ── F. tick-window attempts vs W1-cohort follow-ups ────────────────
-- A follow-up attempted during W1 may belong to an observation made
-- BEFORE W1. These are different populations and are counted apart.
SELECT 'F_ATTEMPT_PROVENANCE', k, v FROM (
    SELECT 'FU_ATTEMPTED_IN_W1_TICKS' AS k,
           coalesce(sum(fu_attempted), 0)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND tick_at <  '2026-09-20T21:58:19Z'::timestamptz
    UNION ALL
    SELECT 'MID_ROWS_WHOSE_OBSERVATION_IS_IN_W1',
           count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
     WHERE o.observed_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND o.observed_at <  '2026-09-20T21:58:19Z'::timestamptz
    UNION ALL
    SELECT 'MID_ROWS_READ_IN_W1_BUT_OBSERVED_EARLIER',
           count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
     WHERE m.read_at >= '2026-09-20T21:28:19Z'::timestamptz
       AND m.read_at <  '2026-09-20T21:58:19Z'::timestamptz
       AND o.observed_at < '2026-09-20T21:28:19Z'::timestamptz
) f

ORDER BY 1, 2
