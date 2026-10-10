-- RC6.3 lifecycle-proof lane, readback 6 (SELECT only): the POST-FIX
-- settlement-lag read on production data (rc6/lifecycle-proof, the fix for
-- review finding 1). Each CTE below is the statement text of
-- profitability/reads.py (LAG_SETTLEMENTS_SQL, LAG_GAME_STARTS_SQL,
-- LAG_WAREHOUSE_STARTS_SQL, LAG_THESIS_STARTS_SQL) with the arrays the code
-- passes: the pre-map first, the warehouse for the markets the pre-map no
-- longer holds, the Xavier thesis for the markets neither holds.

\echo == Q0 server clock
SELECT now() AS server_now;

\echo == Q1 cost of LAG_WAREHOUSE_STARTS_SQL over ALL settled markets (an upper bound, the code passes only the markets without a pre-map start)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (us_market_slug) us_market_slug, event_start_at
  FROM pos_position_economics
 WHERE us_market_slug = ANY(ARRAY(
         SELECT DISTINCT us_market_slug FROM paper_settlements
          WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
            AND outcome IN ('WON', 'LOST'))::text[])
   AND event_start_at IS NOT NULL
   AND book IN ('PAPER', 'ACTUAL')
 ORDER BY us_market_slug, computed_at DESC, revision DESC;

\echo == Q2 the post-fix samples per market (slug, basis, lag hours, recorded at or before now)
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
     WHERE market_slug = ANY(ARRAY(SELECT us_market_slug FROM s)::text[])
       AND game_start IS NOT NULL
     ORDER BY market_slug, updated_at DESC NULLS LAST),
w AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM pos_position_economics
     WHERE us_market_slug = ANY(ARRAY(SELECT us_market_slug FROM s
                                       WHERE us_market_slug NOT IN (SELECT slug FROM p))::text[])
       AND event_start_at IS NOT NULL
       AND book IN ('PAPER', 'ACTUAL')
     ORDER BY us_market_slug, computed_at DESC, revision DESC),
x AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM xavier_entry_theses
     WHERE us_market_slug = ANY(ARRAY(SELECT us_market_slug FROM s
                                       WHERE us_market_slug NOT IN (SELECT slug FROM p)
                                         AND us_market_slug NOT IN (SELECT slug FROM w))::text[])
       AND event_start_at IS NOT NULL
     ORDER BY us_market_slug, recorded_at DESC),
j AS (
    SELECT s.us_market_slug, s.rec, s.settled_at, COALESCE(p.g, w.g, x.g) AS g,
           CASE WHEN p.g IS NOT NULL THEN 'US_PREMAP_GAME_START'
                WHEN w.g IS NOT NULL THEN 'POS_POSITION_ECONOMICS_EVENT_START'
                WHEN x.g IS NOT NULL THEN 'XAVIER_ENTRY_THESIS_EVENT_START'
                ELSE 'NO_GAME_START' END AS basis
      FROM s LEFT JOIN p ON p.slug = s.us_market_slug
             LEFT JOIN w ON w.slug = s.us_market_slug
             LEFT JOIN x ON x.slug = s.us_market_slug)
SELECT us_market_slug, basis, settled_at, g AS game_start,
       round((extract(epoch FROM settled_at - g) / 3600.0)::numeric, 3) AS lag_h,
       rec <= extract(epoch FROM now()) AS recorded_by_now
  FROM j ORDER BY settled_at;

\echo == Q3 what Allie and the economics read from it (settlement_lag counts a lag of at least 0 recorded at or before now, and needs at least 5)
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
     WHERE market_slug = ANY(ARRAY(SELECT us_market_slug FROM s)::text[])
       AND game_start IS NOT NULL
     ORDER BY market_slug, updated_at DESC NULLS LAST),
w AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM pos_position_economics
     WHERE us_market_slug = ANY(ARRAY(SELECT us_market_slug FROM s
                                       WHERE us_market_slug NOT IN (SELECT slug FROM p))::text[])
       AND event_start_at IS NOT NULL
       AND book IN ('PAPER', 'ACTUAL')
     ORDER BY us_market_slug, computed_at DESC, revision DESC),
x AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug, event_start_at AS g
      FROM xavier_entry_theses
     WHERE us_market_slug = ANY(ARRAY(SELECT us_market_slug FROM s
                                       WHERE us_market_slug NOT IN (SELECT slug FROM p)
                                         AND us_market_slug NOT IN (SELECT slug FROM w))::text[])
       AND event_start_at IS NOT NULL
     ORDER BY us_market_slug, recorded_at DESC),
j AS (
    SELECT s.rec, extract(epoch FROM s.settled_at - COALESCE(p.g, w.g, x.g)) AS lag_s,
           CASE WHEN p.g IS NOT NULL THEN 'US_PREMAP_GAME_START'
                WHEN w.g IS NOT NULL THEN 'POS_POSITION_ECONOMICS_EVENT_START'
                WHEN x.g IS NOT NULL THEN 'XAVIER_ENTRY_THESIS_EVENT_START'
                ELSE 'NO_GAME_START' END AS basis
      FROM s LEFT JOIN p ON p.slug = s.us_market_slug
             LEFT JOIN w ON w.slug = s.us_market_slug
             LEFT JOIN x ON x.slug = s.us_market_slug)
SELECT count(*) AS settled_markets,
       count(*) FILTER (WHERE lag_s IS NOT NULL) AS samples_returned,
       count(*) FILTER (WHERE lag_s >= 0 AND rec <= extract(epoch FROM now())) AS samples_settlement_lag_counts,
       count(*) FILTER (WHERE basis = 'US_PREMAP_GAME_START') AS from_premap,
       count(*) FILTER (WHERE basis = 'POS_POSITION_ECONOMICS_EVENT_START') AS from_warehouse,
       count(*) FILTER (WHERE basis = 'XAVIER_ENTRY_THESIS_EVENT_START') AS from_thesis,
       count(*) FILTER (WHERE basis = 'NO_GAME_START') AS without_start,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY lag_s) FILTER (
             WHERE lag_s >= 0 AND rec <= extract(epoch FROM now())) / 3600.0)::numeric, 3) AS median_lag_h,
       count(*) FILTER (WHERE lag_s IS NOT NULL AND basis = 'US_PREMAP_GAME_START') AS pre_fix_samples
  FROM j;

\echo == Q4 the one settled market without any recorded start (why it gives no sample)
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, group_id, settled_at, outcome
      FROM paper_settlements
     WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST')
     ORDER BY us_market_slug, settled_at)
SELECT s.us_market_slug, s.group_id, s.settled_at, s.outcome,
       (SELECT count(*) FROM pos_position_economics e WHERE e.us_market_slug = s.us_market_slug) AS warehouse_rows,
       (SELECT count(*) FROM xavier_entry_theses t WHERE t.us_market_slug = s.us_market_slug) AS theses,
       (SELECT count(*) FROM us_premap m WHERE m.market_slug = s.us_market_slug) AS premap_rows
  FROM s
 WHERE NOT EXISTS (SELECT 1 FROM us_premap m WHERE m.market_slug = s.us_market_slug AND m.game_start IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM pos_position_economics e WHERE e.us_market_slug = s.us_market_slug
                      AND e.event_start_at IS NOT NULL AND e.book IN ('PAPER', 'ACTUAL'))
   AND NOT EXISTS (SELECT 1 FROM xavier_entry_theses t WHERE t.us_market_slug = s.us_market_slug
                      AND t.event_start_at IS NOT NULL);
