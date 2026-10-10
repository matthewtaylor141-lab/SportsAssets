-- Freshness root-cause audit (group freshness), read only.
-- The one interval in which production ran the freshness task (the RC6.1
-- plane, FRESHNESS_SAMPLE events 2026-10-09 14:42:26Z .. 16:01:10Z): what the
-- priority rate, the book reads and the frozen-window samples were, against
-- the hours before it (732cc0c6) and after it (732cc0c6 again).
--   F1  SNAPSHOT events in 20 minute buckets 12:00-17:00Z: rate, reads/min
--   F2  the 78 FRESHNESS_SAMPLE events: state codes (S stream, R refresh,
--       P paper, N not current, U not held, X external) summed per 10 min
--   F3  the frozen windows: members, tiers, membership size
\echo === F1. SNAPSHOT events in 20 minute buckets, 2026-10-09 12:00-17:00Z ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         (payload #>> '{freshness,priority_universe,rate}')::float8 AS rate,
         (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads,
         (payload #>> '{freshness,priority_universe,active_refresh,budget,reads_in_window}')::int AS riw,
         (payload ? 'freshness_task') AS has_task
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at >= timestamptz '2026-10-09 12:00:00+00'
     AND at < timestamptz '2026-10-09 17:00:00+00')
SELECT to_timestamp(floor(extract(epoch FROM at) / 1200) * 1200) AS bucket20m, count(*) AS snaps,
       round(avg(den)::numeric, 1) AS den, round(avg(nc)::numeric, 1) AS nc,
       round(min(rate)::numeric, 4) AS min_rate, round(avg(rate)::numeric, 4) AS avg_rate,
       round(max(rate)::numeric, 4) AS max_rate,
       count(*) FILTER (WHERE rate >= 0.95) AS ge95,
       max(reads) - min(reads) AS reads_in_bucket, max(riw) AS max_reads_in_window,
       bool_or(has_task) AS task_digest
  FROM s GROUP BY 1 ORDER BY 1;
\echo === F2. FRESHNESS_SAMPLE events: state codes summed per 10 minutes ===
WITH f AS (
  SELECT at, payload ->> 'codes' AS codes, (payload ->> 'n')::int AS n,
         payload -> 'counts' AS counts
    FROM market_plane_events WHERE kind = 'FRESHNESS_SAMPLE')
SELECT to_timestamp(floor(extract(epoch FROM at) / 600) * 600) AS bucket10m, count(*) AS samples,
       round(avg(n)::numeric, 1) AS members,
       round(avg(length(codes) - length(replace(codes, 'S', '')))::numeric, 1) AS avg_S_stream,
       round(avg(length(codes) - length(replace(codes, 'R', '')))::numeric, 1) AS avg_R_refresh,
       round(avg(length(codes) - length(replace(codes, 'P', '')))::numeric, 1) AS avg_P_paper,
       round(avg(length(codes) - length(replace(codes, 'N', '')))::numeric, 1) AS avg_N_not_current,
       round(avg(length(codes) - length(replace(codes, 'U', '')))::numeric, 1) AS avg_U_not_held,
       round(avg(length(codes) - length(replace(codes, 'X', '')))::numeric, 1) AS avg_X_external,
       round(avg((length(codes) - length(replace(codes, 'S', '')) + length(codes) - length(replace(codes, 'R', ''))
             + length(codes) - length(replace(codes, 'P', '')))::numeric
             / nullif(length(codes) - (length(codes) - length(replace(codes, 'X', ''))), 0)), 4) AS avg_rate_excl_X,
       round(avg((length(codes) - length(replace(codes, 'S', '')) + length(codes) - length(replace(codes, 'R', ''))
             + length(codes) - length(replace(codes, 'P', '')))::numeric / nullif(length(codes), 0)), 4) AS avg_rate_incl_X
  FROM f GROUP BY 1 ORDER BY 1;
\echo === F2b. the sample reasons summed over all 78 samples ===
SELECT r.key AS reason, sum(r.value::int) AS member_samples
  FROM market_plane_events e, jsonb_each_text(e.payload -> 'reasons') r
 WHERE e.kind = 'FRESHNESS_SAMPLE' GROUP BY 1 ORDER BY 2 DESC LIMIT 15;
\echo === F3. frozen windows ===
SELECT event_key, at, (payload ->> 'n')::int AS members, payload ->> 'membership_hash' AS hash,
       (SELECT count(*) FROM jsonb_array_elements(payload -> 'members') m WHERE m ->> 1 = 'HELD_POSITION') AS held,
       (SELECT count(*) FROM jsonb_array_elements(payload -> 'members') m WHERE m ->> 1 = 'WORKING_ORDER') AS working_orders,
       (SELECT count(*) FROM jsonb_array_elements(payload -> 'members') m WHERE m ->> 1 = 'CANDIDATE') AS candidates
  FROM market_plane_events WHERE kind = 'FRESHNESS_WINDOW' ORDER BY at;
