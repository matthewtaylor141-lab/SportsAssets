-- RC6.3 lifecycle-proof lane, readback 5 (SELECT only).
-- Review finding (research-sql 38014993180): the one-pass settlement-lag read
-- takes each settled market game start from us_premap only, and premap
-- deletes rows unseen for 26 h, so production returns 1 sample (< 5).
-- This readback sizes the durable event-start sources (the warehouse
-- pos_position_economics, Xavier entry theses), plans the fallback reads
-- the fix issues, and counts the samples the fixed read returns today.
-- The CTEs below are the statements in profitability/reads.py
-- (LAG_SETTLEMENTS_SQL, LAG_GAME_STARTS_SQL, LAG_WAREHOUSE_STARTS_SQL,
-- LAG_THESIS_STARTS_SQL) with the slug arrays the code passes.

\echo == P0 server clock and version
SELECT now() AS server_now, version();

\echo == P1 table sizes
SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       pg_size_pretty(pg_relation_size(relid)) AS heap_size
  FROM pg_stat_user_tables
 WHERE relname IN ('pos_position_economics', 'xavier_entry_theses', 'us_premap', 'paper_settlements')
 ORDER BY relname;

\echo == P2 pos_position_economics rows by book with a non-null event start
SELECT book, count(*) AS rows_total, count(*) FILTER (WHERE event_start_at IS NOT NULL) AS with_event_start,
       count(DISTINCT us_market_slug) AS markets
  FROM pos_position_economics GROUP BY book ORDER BY book;

\echo == P3 indexes on the fallback tables
SELECT tablename, indexname, indexdef FROM pg_indexes
 WHERE tablename IN ('pos_position_economics', 'xavier_entry_theses') ORDER BY 1, 2;

\echo == P4 plan, warehouse fallback read for the settled markets without a premap game start
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (us_market_slug) us_market_slug, event_start_at
  FROM pos_position_economics
 WHERE us_market_slug = ANY(ARRAY(
         SELECT DISTINCT s.us_market_slug FROM paper_settlements s
          WHERE s.settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
            AND s.outcome IN ('WON', 'LOST')
            AND NOT EXISTS (SELECT 1 FROM us_premap p WHERE p.market_slug = s.us_market_slug
                               AND p.game_start IS NOT NULL))::text[])
   AND event_start_at IS NOT NULL AND book IN ('PAPER', 'ACTUAL')
 ORDER BY us_market_slug, computed_at DESC, revision DESC;

\echo == P5 plan, Xavier thesis fallback read (all settled markets, an upper bound of its cost)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (us_market_slug) us_market_slug, event_start_at
  FROM xavier_entry_theses
 WHERE us_market_slug = ANY(ARRAY(
         SELECT DISTINCT us_market_slug FROM paper_settlements
          WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
            AND outcome IN ('WON', 'LOST'))::text[])
   AND event_start_at IS NOT NULL
 ORDER BY us_market_slug, recorded_at DESC;

\echo == P6 the fixed read today, samples by game-start basis (lag_ok = lag >= 0 and recorded at or before now, the settlement_lag filter)
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug,
           extract(epoch FROM recorded_at)::float8 AS rec, settled_at
      FROM paper_settlements
     WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
       AND outcome IN ('WON', 'LOST')
     ORDER BY us_market_slug, settled_at),
p AS (
    SELECT DISTINCT ON (market_slug) market_slug AS slug, game_start AS g
      FROM us_premap
     WHERE market_slug IN (SELECT us_market_slug FROM s) AND game_start IS NOT NULL
     ORDER BY market_slug, updated_at DESC NULLS LAST),
w AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM pos_position_economics
     WHERE us_market_slug IN (SELECT us_market_slug FROM s WHERE us_market_slug NOT IN (SELECT slug FROM p))
       AND event_start_at IS NOT NULL AND book IN ('PAPER', 'ACTUAL')
     ORDER BY us_market_slug, computed_at DESC, revision DESC),
x AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM xavier_entry_theses
     WHERE us_market_slug IN (SELECT us_market_slug FROM s
                               WHERE us_market_slug NOT IN (SELECT slug FROM p)
                                 AND us_market_slug NOT IN (SELECT slug FROM w))
       AND event_start_at IS NOT NULL
     ORDER BY us_market_slug, recorded_at DESC),
j AS (
    SELECT s.us_market_slug, s.rec, s.settled_at,
           COALESCE(p.g, w.g, x.g) AS g,
           CASE WHEN p.g IS NOT NULL THEN 'US_PREMAP_GAME_START'
                WHEN w.g IS NOT NULL THEN 'POS_POSITION_ECONOMICS_EVENT_START'
                WHEN x.g IS NOT NULL THEN 'XAVIER_ENTRY_THESIS_EVENT_START'
                ELSE 'NO_GAME_START' END AS basis
      FROM s LEFT JOIN p ON p.slug = s.us_market_slug
             LEFT JOIN w ON w.slug = s.us_market_slug
             LEFT JOIN x ON x.slug = s.us_market_slug)
SELECT basis, count(*) AS markets,
       count(*) FILTER (WHERE g IS NOT NULL AND settled_at >= g AND rec <= extract(epoch FROM now())) AS lag_ok,
       count(*) FILTER (WHERE g IS NOT NULL AND settled_at < g) AS negative_lag,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM settled_at - g)) / 3600.0)::numeric, 3) AS median_lag_h,
       round((min(extract(epoch FROM settled_at - g)) / 3600.0)::numeric, 3) AS min_lag_h,
       round((max(extract(epoch FROM settled_at - g)) / 3600.0)::numeric, 3) AS max_lag_h
  FROM j GROUP BY ROLLUP (basis) ORDER BY basis NULLS LAST;

\echo == P7 consistency, settled markets whose PAPER or ACTUAL warehouse rows record more than one distinct event start
SELECT count(*) AS markets_with_several_starts,
       max(n) AS max_distinct_starts,
       round((max(spread_s) / 3600.0)::numeric, 3) AS max_spread_h
  FROM (SELECT e.us_market_slug, count(DISTINCT e.event_start_at) AS n,
               extract(epoch FROM max(e.event_start_at) - min(e.event_start_at)) AS spread_s
          FROM pos_position_economics e
         WHERE e.event_start_at IS NOT NULL AND e.book IN ('PAPER', 'ACTUAL')
           AND e.us_market_slug IN (SELECT us_market_slug FROM paper_settlements
                                     WHERE settled_at >= now() - interval '180 days'
                                       AND outcome IN ('WON', 'LOST'))
         GROUP BY e.us_market_slug HAVING count(DISTINCT e.event_start_at) > 1) z;

\echo == P8 the basis the warehouse recorded for its event start (release_basis prefix of the latest row with a start)
SELECT CASE WHEN release_basis LIKE 'EVENT_START(US_PREMAP_GAME_START)%' THEN 'US_PREMAP_GAME_START'
            WHEN release_basis LIKE 'EVENT_START(XAVIER_ENTRY_THESIS_EVENT_START)%' THEN 'XAVIER_ENTRY_THESIS_EVENT_START'
            WHEN release_basis IS NULL THEN 'NO_RELEASE_BASIS_RECORDED'
            ELSE 'OTHER' END AS recorded_basis, count(*) AS markets
  FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, release_basis
          FROM pos_position_economics
         WHERE event_start_at IS NOT NULL AND book IN ('PAPER', 'ACTUAL')
           AND us_market_slug IN (SELECT us_market_slug FROM paper_settlements
                                   WHERE settled_at >= now() - interval '180 days'
                                     AND outcome IN ('WON', 'LOST'))
         ORDER BY us_market_slug, computed_at DESC, revision DESC) z
 GROUP BY 1 ORDER BY 1;
