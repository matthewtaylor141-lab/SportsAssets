-- READ-ONLY. RC6.2 lane p-xavier (fix stage). The diagnosis found 622 of
-- 2,287 PinnAPI stamps of held contracts (72 h) with NO review of the
-- contract inside 30 s, and named the paper pass as the suspect: a held
-- change notified while a pass runs is refused busy (the pass holds the
-- paper lock), retried every 5 s for at most 30 s, then dropped; the pass's
-- own Xavier step built its due list before the change. The held-review
-- scheduler counters are in memory only. Here, from what IS persisted: how
-- long each recent paper pass held the lock and how much idle time it left
-- between passes (paper_session_health.recent_heartbeats, the newest 20),
-- the latest pass digest, and the due-to-review latency of every
-- feed-change (MARKET_EVENT) assessment of a paper position, 72 h.
-- Every statement is a SELECT.

\echo P1 the newest recorded paper passes per session: start, elapsed, gap to the next start, idle after it
WITH hb AS (
  SELECT h.session_id, (x->>'at')::float8 AS at,
         (x->>'elapsed_s')::float8 AS elapsed_s, x->>'ok' AS ok,
         x->'summary' AS summary
    FROM paper_session_health h,
         jsonb_array_elements(h.recent_heartbeats) x)
SELECT session_id, to_timestamp(at) AS started, elapsed_s,
       round((lead(at) OVER w - at)::numeric, 3) AS gap_to_next_s,
       round((lead(at) OVER w - at - elapsed_s)::numeric, 3) AS idle_after_s,
       ok, left(summary::text, 160) AS summary
  FROM hb
WINDOW w AS (PARTITION BY session_id ORDER BY at)
 ORDER BY session_id, at;

\echo P2 per session: passes recorded, share of the span the pass held the paper lock, elapsed p50 / max, passes over 20 s and over 30 s
WITH hb AS (
  SELECT h.session_id, (x->>'at')::float8 AS at,
         (x->>'elapsed_s')::float8 AS elapsed_s
    FROM paper_session_health h,
         jsonb_array_elements(h.recent_heartbeats) x)
SELECT session_id, count(*) AS passes,
       to_timestamp(min(at)) AS first_start, to_timestamp(max(at)) AS last_start,
       round((sum(elapsed_s) / nullif(max(at + elapsed_s) - min(at), 0))
             ::numeric, 3) AS held_share,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY elapsed_s)::numeric, 3)
         AS elapsed_p50_s,
       round(max(elapsed_s)::numeric, 3) AS elapsed_max_s,
       count(*) FILTER (WHERE elapsed_s > 20) AS over_20s,
       count(*) FILTER (WHERE elapsed_s > 30) AS over_30s
  FROM hb GROUP BY 1 ORDER BY 1;

\echo P3 the latest pass attempt (ingestion_state paper_session_last_pass): trigger, elapsed, budget, refusal, the Xavier step's scalars
SELECT value->>'written_at' AS written_at, value->>'trigger' AS trigger,
       value->>'elapsed_s' AS elapsed_s,
       value->>'budget_exhausted' AS budget_exhausted,
       value->>'refusal' AS refusal, value->>'why' AS why,
       left((value->'steps'->'xavier')::text, 400) AS xavier_step,
       left((value->'errors')::text, 300) AS errors
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo P4 PAPER MARKET_EVENT assessments (72 h): due-to-review latency buckets and the evidence they read
SELECT CASE WHEN review_latency_s IS NULL THEN 'unknown'
            WHEN review_latency_s <= 5 THEN 'a <=5s'
            WHEN review_latency_s <= 15 THEN 'b 5-15s'
            WHEN review_latency_s <= 30 THEN 'c 15-30s'
            WHEN review_latency_s <= 60 THEN 'd 30-60s'
            WHEN review_latency_s <= 120 THEN 'e 60-120s'
            ELSE 'f >120s' END AS latency,
       count(*) AS assessments,
       count(*) FILTER (WHERE evidence_state = 'FRESH_CURRENT_PROBABILITY')
         AS fresh,
       count(DISTINCT group_id) AS groups
  FROM xavier_management_assessments
 WHERE position_kind = 'PAPER' AND trigger = 'MARKET_EVENT'
   AND assessed_at > now() - interval '72 hours'
 GROUP BY 1 ORDER BY 1;

\echo P5 PAPER assessments (72 h) by trigger: count, latency p50 / p90, within 30 s
SELECT trigger, count(*) AS assessments,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY review_latency_s)
             ::numeric, 3) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY review_latency_s)
             ::numeric, 3) AS p90_s,
       count(*) FILTER (WHERE review_latency_s <= 30) AS within_30s,
       count(*) FILTER (WHERE evidence_state = 'FRESH_CURRENT_PROBABILITY')
         AS fresh
  FROM xavier_management_assessments
 WHERE position_kind = 'PAPER' AND assessed_at > now() - interval '72 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo P6 the persisted feed heartbeat: the held watch block (cumulative since the feed process started)
SELECT value->>'beat_at' AS beat_at, value->>'state' AS state,
       left((value->'held_priority_targets')::text, 700) AS held_watch
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
