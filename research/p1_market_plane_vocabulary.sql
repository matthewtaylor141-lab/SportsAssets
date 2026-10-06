-- READ-ONLY. The venue catalogue's vocabulary as BETTOR holds it (us_premap):
-- markets by sports_type / kind / listing_state / league, the active count,
-- and the latest completeness receipts per lane. Every statement is a SELECT.
SELECT listing_state, count(DISTINCT market_slug) AS markets,
       max(updated_at) AS newest
  FROM us_premap GROUP BY 1 ORDER BY 2 DESC;

SELECT sports_type, kind, count(DISTINCT market_slug) AS markets
  FROM us_premap
 WHERE updated_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 120;

SELECT coalesce(team_league, split_part(market_slug, '-', 2)) AS league,
       count(DISTINCT market_slug) AS markets
  FROM us_premap
 WHERE updated_at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 80;

SELECT DISTINCT ON (lane) lane, finished_at, outcome, truncated, pages_read,
       requests, events_seen, markets_seen, markets_kept
  FROM venue_catalogue_receipts ORDER BY lane, finished_at DESC;

SELECT count(DISTINCT market_slug) AS markets_6h,
       count(DISTINCT market_slug) FILTER (WHERE game_start > now() - interval '12 hours') AS upcoming_or_recent
  FROM us_premap WHERE updated_at > now() - interval '6 hours';
