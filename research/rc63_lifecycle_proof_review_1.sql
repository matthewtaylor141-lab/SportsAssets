-- RC6.3 lifecycle-proof REVIEW readback 1 (SELECT only).
-- Reviewer check of rc6/lifecycle-proof efb3778d (profitability/reads.py
-- settlement_lag_samples, one-pass): the lane read on production now -- its
-- cost, and how many settlement-lag samples it returns; whether the settled
-- markets still have us_premap rows (premap prunes rows unseen 26 h); and the
-- durable event starts recorded for the same settled groups.

\echo == V1 the lane read, statement 1 (first WON or LOST settlement per market, 180 d)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (us_market_slug) us_market_slug,
       extract(epoch FROM recorded_at)::float8 AS rec, settled_at
  FROM paper_settlements
 WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
   AND outcome IN ('WON', 'LOST')
 ORDER BY us_market_slug, settled_at;

\echo == V2 the lane read, statement 2 (latest mapped game start of those markets, one pass)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (market_slug) market_slug, game_start
  FROM us_premap
 WHERE market_slug = ANY(ARRAY(
         SELECT DISTINCT us_market_slug FROM paper_settlements
          WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
            AND outcome IN ('WON', 'LOST'))::text[])
   AND game_start IS NOT NULL
 ORDER BY market_slug, updated_at DESC NULLS LAST;

\echo == V3 settled markets (180 d, WON or LOST) and their event-start coverage by source
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, group_id, settled_at, recorded_at
      FROM paper_settlements
     WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST')
     ORDER BY us_market_slug, settled_at),
g AS (SELECT DISTINCT us_market_slug, group_id FROM paper_settlements
       WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST'))
SELECT count(*) AS settled_markets,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM us_premap p
                                       WHERE p.market_slug = s.us_market_slug)) AS with_any_premap_row,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM us_premap p
                                       WHERE p.market_slug = s.us_market_slug
                                         AND p.game_start IS NOT NULL)) AS with_premap_game_start,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM g JOIN xavier_entry_theses x
                                          ON x.group_id = g.group_id
                                       WHERE g.us_market_slug = s.us_market_slug
                                         AND x.event_start_at IS NOT NULL)) AS with_xavier_thesis_event_start,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM pos_position_economics e
                                       WHERE e.us_market_slug = s.us_market_slug
                                         AND e.event_start_at IS NOT NULL)) AS with_warehouse_event_start,
       min(settled_at) AS first_settled, max(settled_at) AS last_settled
  FROM s;

\echo == V4 settled markets per settle day with a premap game start (how fast the sample decays)
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, settled_at
      FROM paper_settlements
     WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST')
     ORDER BY us_market_slug, settled_at)
SELECT date_trunc('day', settled_at) AS settle_day, count(*) AS markets,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM us_premap p
                                       WHERE p.market_slug = s.us_market_slug
                                         AND p.game_start IS NOT NULL)) AS with_premap_game_start
  FROM s GROUP BY 1 ORDER BY 1;

\echo == V5 the lag samples the lane read returns now (lag hours per mapped settled market)
WITH s AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, settled_at, recorded_at
      FROM paper_settlements
     WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST')
     ORDER BY us_market_slug, settled_at),
g AS (
    SELECT DISTINCT ON (market_slug) market_slug, game_start, updated_at
      FROM us_premap
     WHERE market_slug IN (SELECT us_market_slug FROM s) AND game_start IS NOT NULL
     ORDER BY market_slug, updated_at DESC NULLS LAST)
SELECT s.us_market_slug, s.settled_at, g.game_start, g.updated_at AS premap_updated_at,
       round((extract(epoch FROM s.settled_at - g.game_start) / 3600.0)::numeric, 3) AS lag_h
  FROM s JOIN g ON g.market_slug = s.us_market_slug ORDER BY s.settled_at;

\echo == V6 server version
SELECT version();
