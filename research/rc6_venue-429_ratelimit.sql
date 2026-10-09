-- READ-ONLY. RC6 lane P0-429 (venue rate-limit recovery): the 429 picture
-- the workers' own records hold, before and after the escalating cooldown.
--
-- BEFORE the lane deploys: S1's cooldown columns are NULL (the heartbeats do
-- not carry them yet) and S2/S3 are the baseline -- how many of each tick's
-- reads the venue refused, by hour. AFTER: S1 names the process-wide cooldown
-- (in force or not, consecutive 429s, deferred and skipped reads per source,
-- the last 429) on every walker's beat and the data-api host's on the
-- poller's; S2 shows ticks stopped by name (status venue_429_cooldown) and
-- the rate-limited share falling. Counters and statuses only: no wallet,
-- account, key or order field is selected.

\echo S1 every gateway walkers beat: status, age, its own 429 counters, the cooldown readback
SELECT service, status, beat_at,
       round(extract(epoch FROM now() - beat_at)) AS age_s,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           ->> 'cooldown_active'                         AS cooldown_active,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           ->> 'consecutive_429'                         AS consecutive_429,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           ->> 'rate_limited_total'                      AS rate_limited_total,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           ->> 'deferred_total'                          AS deferred_total,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           ->> 'escalations'                             AS escalations,
       COALESCE(detail->'venueRateLimit', detail->'venue_rate_limit')
           -> 'last_429' ->> 'at_epoch'                  AS last_429_at_epoch,
       detail->>'rateLimited'                            AS bettor_state_rate_limited,
       detail->>'read'                                   AS bettor_state_read,
       COALESCE(detail->>'obsSkippedCooldown', detail->>'skippedCooldown',
                detail->>'skipped_cooldown_markets')     AS skipped_cooldown,
       detail->>'unreadable'                             AS unreadable,
       detail->>'abandoned'                              AS abandoned
  FROM service_heartbeats
 WHERE service IN ('bettor_state', 'shadow_bettor', 'shadow_rn1',
                   'shadow_experimental', 'mirror_shadow', 'institutional_md')
 ORDER BY service;

\echo S2 the per-source split of the cooldown readback (post-deploy; one walkers beat carries the process view)
SELECT h.service, s.key AS source,
       s.value->>'rate_limited'      AS rate_limited,
       s.value->>'deferred'          AS deferred,
       s.value->>'skipped_by_walker' AS skipped_by_walker,
       s.value->'last_429'->>'path'  AS last_429_path,
       s.value->'last_429'->>'at_epoch' AS last_429_at_epoch
  FROM service_heartbeats h,
       jsonb_each(COALESCE(h.detail->'venueRateLimit',
                           h.detail->'venue_rate_limit', '{}'::jsonb)
                  -> 'by_source') AS s(key, value)
 WHERE h.service = 'bettor_state'
 ORDER BY s.key;

\echo S3 the data-api hosts cooldown on the pollers beat (post-deploy)
SELECT service, status, beat_at,
       detail->'data_api_rate_limit'->>'host'                  AS host,
       detail->'data_api_rate_limit'->>'cooldown_active'       AS cooldown_active,
       detail->'data_api_rate_limit'->>'consecutive_429'       AS consecutive_429,
       detail->'data_api_rate_limit'->>'rate_limited_total'    AS rate_limited_total,
       detail->'data_api_rate_limit'->>'escalations'           AS escalations,
       detail->'data_api_rate_limit'->>'normal_waiters_held'   AS normal_waiters_held,
       detail->'data_api_rate_limit'->'last_429'->>'at_epoch'  AS last_429_at_epoch
  FROM service_heartbeats WHERE service = 'poller';

\echo S4 bettor_states own tick records, by hour (12 h): reads, the venues 429s, and ticks stopped by the cooldown
SELECT date_trunc('hour', tick_at) AS hour,
       count(*)                                        AS ticks,
       sum(obs_attempted)                              AS obs_attempted,
       sum(obs_rate_limited)                           AS obs_rate_limited,
       round(100.0 * sum(obs_rate_limited)
             / NULLIF(sum(obs_attempted), 0), 1)       AS rate_limited_pct,
       sum(fu_attempted)                               AS fu_attempted,
       sum(obs_never_attempted)                        AS obs_never_attempted,
       count(*) FILTER (WHERE status = 'venue_429_cooldown') AS ticks_stopped_by_cooldown,
       count(*) FILTER (WHERE status = 'venue_unreadable')   AS ticks_abandoned_unreadable
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1 ORDER BY 1;
