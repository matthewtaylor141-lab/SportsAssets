-- READ-ONLY. RC6 lane P0-429 (venue rate-limit recovery): the readback that
-- proves the escalating 429 cooldown on the DEPLOYED system, one process at a
-- time. Before the lane deploys every cooldown column is NULL (the beats do
-- not carry it yet); after, each row names its process's cooldown.
--
--   workers  every gateway walker's service_heartbeats.detail carries
--            venueRateLimit (mirror_shadow: venue_rate_limit) -- the
--            process-wide cooldown, consecutive 429s, deferred and skipped
--            reads per source, the last 429;
--   workers  the poller's beat carries data_api_rate_limit (the data-api
--            host's cooldown, fed by polite_get and whale_exits);
--   api      ingestion_state 'ext_pinnacle_last_cycle' carries
--            venue_rate_controls.process_request_totals.escalating_429_cooldown.
--
-- Counters, statuses and paths only: no wallet, account, key or order field.

\echo R1 workers: every gateway walker's beat and the process cooldown it carries
SELECT service, status, beat_at,
       round(extract(epoch FROM now() - beat_at)) AS age_s,
       c->>'version'                AS version,
       c->>'cooldown_active'        AS cooldown_active,
       c->>'cooldown_seconds_left'  AS seconds_left,
       c->>'level_s'                AS level_s,
       c->>'consecutive_429'        AS consecutive_429,
       c->>'rate_limited_total'     AS rate_limited_total,
       c->>'escalations'            AS escalations,
       c->>'in_flight_429s'         AS in_flight_429s,
       c->>'resets'                 AS resets,
       c->>'deferred_total'         AS deferred_total,
       c->>'priority_waits'         AS priority_waits,
       c->>'deadline_waits'         AS deadline_waits,
       c->'last_429'->>'path'       AS last_429_path,
       to_timestamp((c->'last_429'->>'at_epoch')::double precision) AS last_429_at,
       COALESCE(detail->>'obsSkippedCooldown', detail->>'skippedCooldown',
                detail->>'skipped_cooldown_markets')   AS skipped_this_pass
  FROM (SELECT service, status, beat_at, detail,
               COALESCE(detail->'venueRateLimit',
                        detail->'venue_rate_limit') AS c
          FROM service_heartbeats
         WHERE service IN ('bettor_state', 'shadow_bettor', 'shadow_rn1',
                           'shadow_experimental', 'mirror_shadow',
                           'institutional_md')) b
 ORDER BY service;

\echo R2 workers: the per-source split (each walker's beat carries the same process view; bettor_state's is read)
SELECT h.service, s.key AS source,
       s.value->>'rate_limited'      AS rate_limited,
       s.value->>'deferred'          AS deferred,
       s.value->>'skipped_by_walker' AS skipped_by_walker,
       s.value->'last_429'->>'path'  AS last_429_path,
       to_timestamp((s.value->'last_429'->>'at_epoch')::double precision) AS last_429_at
  FROM service_heartbeats h,
       jsonb_each(COALESCE(h.detail->'venueRateLimit',
                           h.detail->'venue_rate_limit', '{}'::jsonb)
                  -> 'by_source') AS s(key, value)
 WHERE h.service = 'bettor_state'
 ORDER BY s.key;

\echo R3 workers: the data-api host cooldown on the poller beat
SELECT service, status, beat_at,
       d->>'version'                 AS version,
       d->>'host'                    AS host,
       d->>'cooldown_active'         AS cooldown_active,
       d->>'cooldown_seconds_left'   AS seconds_left,
       d->>'consecutive_429'         AS consecutive_429,
       d->>'rate_limited_total'      AS rate_limited_total,
       d->>'escalations'             AS escalations,
       d->>'resets'                  AS resets,
       d->>'normal_waiters_held'     AS normal_waiters_held,
       d->>'priority_served_in_cooldown' AS priority_served_in_cooldown,
       d->'last_429'->>'path'        AS last_429_path,
       to_timestamp((d->'last_429'->>'at_epoch')::double precision) AS last_429_at
  FROM (SELECT service, status, beat_at,
               detail->'data_api_rate_limit' AS d
          FROM service_heartbeats WHERE service = 'poller') p;

\echo R4 api: the API process cooldown on the ext_pinnacle cycle heartbeat
SELECT to_timestamp((value->>'at')::double precision)               AS cycle_at,
       value->'venue_rate_controls'->'process_request_totals'->>'dispatched'   AS dispatched,
       value->'venue_rate_controls'->'process_request_totals'->>'rate_limited' AS rate_limited,
       value->'venue_rate_controls'->'process_request_totals'->>'refused_by_gate' AS refused_by_gate,
       e->>'version'               AS version,
       e->>'scope'                 AS scope,
       e->>'cooldown_active'       AS cooldown_active,
       e->>'consecutive_429'       AS consecutive_429,
       e->>'rate_limited_total'    AS rate_limited_total,
       e->>'escalations'           AS escalations,
       e->>'deferred_total'        AS deferred_total,
       e->>'priority_waits'        AS priority_waits,
       e->'last_429'->>'path'      AS last_429_path
  FROM (SELECT value,
               value->'venue_rate_controls'->'process_request_totals'
                    ->'escalating_429_cooldown' AS e
          FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle') x;

\echo R5 bettor_state own tick records by hour (12 h): reads, the venues 429s, ticks the cooldown stopped
SELECT date_trunc('hour', tick_at) AS hour,
       count(*)                                        AS ticks,
       sum(obs_attempted)                              AS obs_attempted,
       sum(obs_rate_limited)                           AS obs_rate_limited,
       round(100.0 * sum(obs_rate_limited)
             / NULLIF(sum(obs_attempted), 0), 1)       AS rate_limited_pct,
       sum(obs_never_attempted)                        AS obs_never_attempted,
       count(*) FILTER (WHERE status = 'venue_429_cooldown') AS ticks_stopped_by_cooldown,
       count(*) FILTER (WHERE status = 'venue_unreadable')   AS ticks_abandoned_unreadable
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1 ORDER BY 1;

\echo R6 loop health of the gateway walkers and the poller (the cooldown status is shown by name, never as a success)
SELECT loop_name, process, cadence_s, last_success_at, last_error_at,
       left(last_error, 120) AS last_error, successes, errors, commit_sha
  FROM runtime_loop_health
 WHERE loop_name IN ('bettor_state', 'shadow_bettor', 'shadow_rn1',
                     'shadow_experimental', 'mirror_shadow',
                     'institutional_md', 'poller')
 ORDER BY loop_name, process;
