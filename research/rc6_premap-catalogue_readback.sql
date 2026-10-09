-- READ-ONLY. RC6 LANE P0-COMPLETENESS: THE POST-DEPLOY READBACK.
--
-- Run after the release carrying the lane deploys (before that every new
-- field reads NULL, which is the point: absent is UNMEASURED, never PASS).
--
--   1 premap: each lane's sweep_completeness (status, complete, events
--     walked vs expected, pages, rows, truncated, resumed / unsent / re-sent
--     page reads, last complete sweep) from its state record
--   2 premap: the last 24 h of receipts by lane, version and outcome, with
--     the page reads that were retried at the same offset
--   3 premap: every full-lane receipt with a retried read or not COMPLETE,
--     last 24 h, with the window pass's stop and error
--   4 gamma metadata: the walk's mode (KEYSET_ROTATION / OFFSET_FALLBACK),
--     the rotation, the last complete rotation, keyset_unavailable, status
--   5 the metadata cache: open markets written in the last hour / day
--   6 the research collector: heartbeat (universe, capBinding,
--     visitsToCoverSlice), rows by universe version over 24 h, and the
--     distinct markets V3 drew from each over-cap slice
--
-- Every statement is a SELECT.

\echo == 1. premap sweep_completeness by lane ==
SELECT key,
       value->>'mode' AS mode,
       value->>'at'   AS at,
       value->'sweep_completeness'->>'status'          AS status,
       value->'sweep_completeness'->>'complete'        AS complete,
       value->'sweep_completeness'->>'events_walked'   AS events_walked,
       value->'sweep_completeness'->>'events_expected' AS events_expected,
       value->'sweep_completeness'->>'pages'           AS pages,
       value->'sweep_completeness'->>'rows'            AS rows_written,
       value->'sweep_completeness'->>'truncated'       AS truncated,
       value->'sweep_completeness'->>'resumed_reads'   AS resumed_reads,
       value->'sweep_completeness'->>'reads_unsent'    AS reads_unsent,
       value->'sweep_completeness'->>'reads_resent'    AS reads_resent,
       value->'sweep_completeness'->>'last_complete_sweep_at'
           AS last_complete_sweep_at,
       left(coalesce(value->'sweep_completeness'->>'error', ''), 140) AS error
  FROM ingestion_state
 WHERE key IN ('premap_last', 'premap_last_fast', 'premap_last_calendar')
 ORDER BY key;

\echo == 2. premap receipts, last 24 h, by lane, version and outcome ==
SELECT lane, version, outcome, count(*) AS receipts,
       sum(coalesce((receipt->'passes'->'WINDOW'->>'resumed_reads')::int, 0)
           + coalesce((receipt->'passes'->'FAST'->>'resumed_reads')::int, 0))
           AS window_resumed_reads,
       sum(coalesce((receipt->'passes'->'WINDOW'->>'reads_unsent')::int, 0)
           + coalesce((receipt->'passes'->'FAST'->>'reads_unsent')::int, 0))
           AS window_reads_unsent,
       max(recorded_at) AS last_at
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY lane, version, outcome
 ORDER BY lane, version, outcome;

\echo == 3. full-lane receipts with a retried read or not COMPLETE, last 24 h ==
SELECT id, recorded_at, version, outcome, pages_read, requests, events_seen,
       round(extract(epoch FROM finished_at - started_at)) AS seconds,
       receipt->'passes'->'WINDOW'->>'stopped'        AS window_stopped,
       receipt->'passes'->'WINDOW'->'read_failures'   AS read_failures,
       receipt->'passes'->'WINDOW'->>'resumed_reads'  AS resumed_reads,
       left(coalesce(receipt->'passes'->'WINDOW'->>'error', ''), 140)
           AS window_error
  FROM venue_catalogue_receipts
 WHERE lane = 'full' AND recorded_at > now() - interval '24 hours'
   AND (outcome <> 'COMPLETE'
        OR coalesce((receipt->'passes'->'WINDOW'->>'resumed_reads')::int, 0) > 0
        OR coalesce((receipt->'passes'->'WINDOW'->>'reads_unsent')::int, 0) > 0)
 ORDER BY recorded_at DESC
 LIMIT 40;

\echo == 4. the gamma metadata walk ==
SELECT status, beat_at,
       detail->'gamma_paging'->>'mode'               AS mode,
       detail->'gamma_paging'->>'stopped'            AS stopped,
       detail->'gamma_paging'->>'truncated'          AS truncated,
       detail->'gamma_paging'->>'requests'           AS requests,
       detail->'gamma_paging'->>'request_budget'     AS request_budget,
       detail->'gamma_paging'->>'catalogue_complete' AS catalogue_complete,
       detail->'gamma_paging'->'rotation'            AS rotation,
       detail->'gamma_paging'->'last_complete_rotation' AS last_complete_rotation,
       detail->'gamma_paging'->'keyset_unavailable'  AS keyset_unavailable,
       detail->'errors'                              AS errors
  FROM service_heartbeats
 WHERE service = 'metadata';

\echo == 5. the metadata cache ==
SELECT count(*) FILTER (WHERE NOT closed) AS open_markets,
       count(*) FILTER (WHERE updated_at > now() - interval '1 hour')
           AS written_last_hour,
       count(*) FILTER (WHERE updated_at > now() - interval '24 hours')
           AS written_last_day,
       count(*) FILTER (WHERE NOT closed AND updated_at
                        < now() - interval '24 hours') AS open_not_written_24h
  FROM markets;

\echo == 6a. the research collector heartbeat ==
SELECT status, beat_at,
       detail->>'universe'           AS universe,
       detail->>'capBinding'         AS cap_binding,
       detail->>'inSlice'            AS in_slice,
       detail->>'windowStart'        AS window_start,
       detail->>'visitsToCoverSlice' AS visits_to_cover_slice,
       detail->>'capPerCycle'        AS cap_per_cycle,
       detail->>'reads'              AS reads
  FROM service_heartbeats
 WHERE service = 'bettor_state';

\echo == 6b. the research collector rows, last 24 h, by version ==
SELECT universe_version, rule_sha, count(*) AS rows_observed,
       count(*) FILTER (WHERE slice_truncated) AS slice_truncated_rows,
       count(DISTINCT market_id) AS markets,
       min(observed_at) AS first_at, max(observed_at) AS last_at
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '24 hours'
 GROUP BY universe_version, rule_sha
 ORDER BY universe_version, rule_sha;

\echo == 6c. the research collector ticks, last 24 h, by version and status ==
SELECT universe_version, status, count(*) AS ticks,
       sum(obs_scheduled) AS scheduled, sum(obs_attempted) AS attempted
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '24 hours'
 GROUP BY universe_version, status
 ORDER BY universe_version, ticks DESC;
