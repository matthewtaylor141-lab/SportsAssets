-- READ-ONLY. RC6 stabilization proof: does current PinnAPI evidence flow
-- provider -> ingestion -> probability qualification -> decision engine ->
-- Xavier, after the deploy that ends the RC5 feed refusal (2026-10-08
-- 19:00:51Z, FEED_EVICTION_LOOP_SUSPECTED)? A connected socket or a fresh
-- heartbeat alone is not the answer: every stage below is read from its own
-- table, in 10-minute buckets over the last 3 hours, so the deploy instant
-- splits each series into before and after. Every statement is a SELECT.

\echo C1 PROVIDER + OWNER: persisted feed heartbeat (state, refusal, authority, stand-down, close records)
SELECT to_timestamp((value->>'beat_at')::float8)       AS beat_at,
       value->>'state'                                 AS state,
       value->>'refused'                               AS refused,
       value->'cache'->'authority'                     AS authority,
       value->'cache'->'counts'                        AS cache_counts,
       value->'eviction_standdown'                     AS standdown,
       value->'eviction_standdown_prior_runtime'       AS standdown_prior,
       value->'client_close_backoff'                   AS client_backoff,
       jsonb_path_query_array(coalesce(value->'transitions','[]'::jsonb), '$[last - 7 to last]') AS last_transitions
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo C2 OWNER: who holds the decider writer lock and the feed lease now
SELECT ((l.classid::bigint << 32) | l.objid::bigint) AS lock_key, l.pid,
       a.application_name, a.backend_start
  FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid = l.pid
 WHERE l.locktype = 'advisory' AND l.granted
   AND ((l.classid::bigint << 32) | l.objid::bigint) IN (7723901544120034, 7723901544120036);

\echo C3 INGESTION: provider-stamp -> receipt distribution and census as the heartbeat reports them
SELECT value->'cache'->'stamp_to_receipt' AS stamp_to_receipt,
       value->'cache'->'events_by_sport'  AS events_by_sport,
       value->'census'                    AS census
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo C4 QUALIFICATION: candidate rows per 10 min: feed refusals vs WS-referenced vs admitted
SELECT to_timestamp(floor(extract(epoch FROM cycle_at) / 600) * 600) AS bucket,
       count(*) AS rows, count(DISTINCT cycle_id) AS cycles,
       count(*) FILTER (WHERE codes ? 'FEED_OWNERSHIP_NOT_HELD' OR first_refusal = 'FEED_OWNERSHIP_NOT_HELD') AS feed_not_held,
       count(*) FILTER (WHERE codes::text LIKE '%FEED_EPOCH_NOT_RESYNCHRONIZED%') AS not_resynced,
       count(*) FILTER (WHERE codes::text LIKE '%FEED_QUOTE_OLDER_THAN_LIMIT%') AS ws_quote_stale,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_on_arrival,
       count(*) FILTER (WHERE first_refusal = 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH') AS deadline_passed,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '3 hours'
 GROUP BY 1 ORDER BY 1;

\echo C5 QUALIFICATION: first refusals in the last 60 min (named, counted)
SELECT stage, first_refusal, count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '60 minutes' AND first_refusal IS NOT NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;

\echo C6 DECISION ENGINE: paper decisions per 10 min, with a Pinnacle probability present
SELECT to_timestamp(floor(extract(epoch FROM decided_at) / 600) * 600) AS bucket,
       count(*) AS decisions,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter,
       count(*) FILTER (WHERE p_pinnacle IS NOT NULL) AS with_p_pinnacle,
       count(*) FILTER (WHERE pinnacle->>'source' ILIKE '%pinnapi%' OR pinnacle::text ILIKE '%pinnapi%') AS pinnapi_sourced,
       max(decided_at) AS newest
  FROM paper_decisions
 WHERE decided_at >= now() - interval '3 hours'
 GROUP BY 1 ORDER BY 1;

\echo C7 DECISION ENGINE: paper decision refusals in the last 60 min
SELECT coalesce(refusal, '(ENTER)') AS refusal, count(*)
  FROM paper_decisions WHERE decided_at >= now() - interval '60 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo C8 XAVIER: latest review per open group (probability source, age, feed refusal, packet complete)
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, trigger, recommendation, refusal, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '3 hours'
   ORDER BY group_id, reviewed_at DESC)
SELECT group_id, reviewed_at, trigger, recommendation, refusal,
       measure->>'evidence_state'      AS evidence_state,
       measure->>'probability_source'  AS probability_source,
       measure->>'probability_age_s'   AS probability_age_s,
       coalesce(measure->>'feed_refusal', '-') AS feed_refusal,
       (selection->'management_packet'->'gate'->>'complete') AS packet_complete,
       selection->'management_packet'->'gate'->'missing'      AS packet_missing
  FROM latest ORDER BY reviewed_at DESC;

\echo C9 XAVIER: reviews per 10 min and how many carried a current PinnAPI probability
SELECT to_timestamp(floor(extract(epoch FROM reviewed_at) / 600) * 600) AS bucket,
       count(*) AS reviews, count(DISTINCT group_id) AS groups,
       count(*) FILTER (WHERE measure->>'evidence_state' = 'CURRENT') AS evidence_current,
       count(*) FILTER (WHERE (selection->'management_packet'->'gate'->>'complete')::boolean) AS packet_complete
  FROM paper_xavier_reviews
 WHERE reviewed_at >= now() - interval '3 hours'
 GROUP BY 1 ORDER BY 1;

\echo C10 LOOP HEALTH: the deciding loop and the feed loop
SELECT loop_name, process, last_start_at, last_success_at, last_error_at,
       left(last_error, 160) AS last_error, starts, successes, errors, commit_sha, updated_at
  FROM runtime_loop_health
 WHERE loop_name LIKE 'ext_pinnacle%' OR loop_name LIKE 'pinnapi%'
 ORDER BY loop_name, process;
