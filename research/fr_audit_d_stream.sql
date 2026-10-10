-- Freshness root-cause audit (group freshness), read only.
--   D1  why the PAPER book holds nothing: strategy lifecycle states now
--   D2  PAPER decisions per day, verdicts and the refusals that stop entries
--   D3  evaluated candidates per hour (what moves the priority denominator)
--   D4  how often a priority member's PMX book is re-sent: the gap between
--       the distinct receipt instants of one symbol in the PRIORITY_PMX_BOOKS
--       events of the last 12 h (a book appears there only while it is
--       inside the 300 s bound; the gap is exact, the resolution is one pass)
--   D5  the plane stream connections: attempts, uptime, filtered updates
\echo === D1. strategy lifecycle states now ===
SELECT account_id, strategy, state, rule_id, actor, recorded_at, left(why, 160) AS why
  FROM paper_strategy_lifecycle_current_v ORDER BY account_id, strategy;
\echo === D2a. PAPER decisions per day, verdict ===
SELECT date_trunc('day', decided_at) AS day, verdict, count(*) AS n
  FROM paper_decisions WHERE decided_at > now() - interval '8 days'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
\echo === D2b. first refusal of the decisions of the last 3 days ===
SELECT refusal, count(*) AS n, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions WHERE decided_at > now() - interval '3 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;
\echo === D3. distinct evaluated candidate markets per hour, last 30 h ===
SELECT date_trunc('hour', cycle_at) AS hr, count(DISTINCT us_market_slug) AS markets, count(*) AS evaluations
  FROM ext_candidate_outcomes WHERE cycle_at > now() - interval '30 hours' AND us_market_slug IS NOT NULL
 GROUP BY 1 ORDER BY 1;
\echo === D4. gap between distinct PMX receipt instants of one priority symbol (last 12 h) ===
WITH b AS (
  SELECT k.key AS sym, (k.value ->> 'received_at')::float8 AS rcv
    FROM market_plane_events ev, jsonb_each(ev.payload -> 'books') AS k
   WHERE ev.kind = 'PRIORITY_PMX_BOOKS' AND ev.at > now() - interval '12 hours'),
d AS (SELECT DISTINCT sym, rcv FROM b WHERE rcv IS NOT NULL),
g AS (SELECT sym, rcv - lag(rcv) OVER (PARTITION BY sym ORDER BY rcv) AS gap FROM d)
SELECT count(*) AS gaps, count(DISTINCT sym) AS symbols,
       count(*) FILTER (WHERE gap < 30) AS lt_30s,
       count(*) FILTER (WHERE gap >= 30 AND gap < 90) AS s30_90,
       count(*) FILTER (WHERE gap >= 90 AND gap < 150) AS s90_150,
       count(*) FILTER (WHERE gap >= 150 AND gap < 300) AS s150_300,
       count(*) FILTER (WHERE gap >= 300 AND gap < 600) AS s300_600,
       count(*) FILTER (WHERE gap >= 600 AND gap < 1200) AS s600_1200,
       count(*) FILTER (WHERE gap >= 1200 AND gap < 3600) AS s1200_3600,
       count(*) FILTER (WHERE gap >= 3600) AS ge_3600,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p90_s,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p99_s
  FROM g WHERE gap IS NOT NULL;
\echo === D5. plane stream connection counters over time (SNAPSHOT events, every 30th) ===
SELECT at,
       payload #>> '{subscription,shards,0,attempts}' AS attempts,
       payload #>> '{subscription,shards,0,state}' AS shard_state,
       payload #>> '{subscription,shards,0,filtered_updates}' AS filtered_updates,
       payload #>> '{subscription,fresh}' AS fresh_books,
       payload #>> '{subscription,stale_subscribed}' AS stale_subscribed
  FROM (SELECT at, payload, row_number() OVER (ORDER BY at) AS rn FROM market_plane_events
         WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 15:59:44+00') x
 WHERE rn % 30 = 1 ORDER BY at;
