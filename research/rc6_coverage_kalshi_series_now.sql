-- RC6 lane D2 (coverage), review findings 1 and 2. Read only.
-- K1/K2: every active Kalshi registry series with the venue's series title
-- and its active rows, so the lane's series rules
-- (market_plane.waterfall.kalshi_series_class) can be run over the current
-- catalogue for the corrected Kalshi tier counts.
-- N1/N2: the venue's non-sports markets by code (event slug first segment,
-- the slug grammar the lane reads): those listed active in us_premap, and
-- those still active registry rows under the RC5 reading -- the rows the
-- lane's grammar keeps out of coverage.active, which the plane's
-- populate_full record will count.
\echo === K1: active Kalshi registry rows by series: [series, title, active rows] x 60 per line ===
WITH s AS (
  SELECT competition AS series,
         max(ontology -> 'series' ->> 'title') AS title,
         count(*) AS n
    FROM market_plane_registry
   WHERE active AND venue = 'KALSHI'
   GROUP BY 1),
b AS (SELECT s.*, (row_number() OVER (ORDER BY series) - 1) / 60 AS grp
        FROM s)
SELECT json_agg(json_build_array(series, title, n) ORDER BY series)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === K2: totals ===
SELECT count(*) AS active_kalshi_rows,
       count(DISTINCT competition) AS series,
       now() AS read_at
  FROM market_plane_registry
 WHERE active AND venue = 'KALSHI';
\echo === N1: us_premap markets by NON_SPORTS code (event slug first segment): listed active within 3 h, and all ===
WITH m AS (
  SELECT market_slug,
         lower(split_part(coalesce(max(event_slug), ''), '-', 1)) AS code,
         bool_or(listing_state IN ('PREGAME', 'LIVE', 'NOT_LIVE', 'STARTED',
                                   'UNKNOWN')) AS listed,
         max(updated_at) AS seen
    FROM us_premap
   WHERE market_slug IS NOT NULL
   GROUP BY market_slug)
SELECT code,
       count(*) FILTER (WHERE listed AND seen > now() - interval '3 hours')
         AS listed_active,
       count(*) AS all_markets
  FROM m
 WHERE code IN ('btc', 'eth', 'sol', 'ntflx', 'nobel', 'temp', 'gtasc',
                'oscars', 'emmys', 'grammys', 'box', 'pol', 'us', 'uscpi',
                'uscpicore', 'usfed', 'usgas', 'usunemp', 'usnfp', 'fed',
                'cut', 'hike', 'ecb', 'boj', 'boe', 'boc', 'boi', 'bcb',
                'cbr')
 GROUP BY 1 ORDER BY 2 DESC, 1;
\echo === N2: active POLYMARKET_US registry rows whose event id's first segment is a NON_SPORTS code (in coverage.active under RC5) ===
SELECT lower(split_part(coalesce(event_id, ''), '-', 1)) AS code,
       count(*) AS active_registry_rows
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND lower(split_part(coalesce(event_id, ''), '-', 1)) IN (
       'btc', 'eth', 'sol', 'ntflx', 'nobel', 'temp', 'gtasc', 'oscars',
       'emmys', 'grammys', 'box', 'pol', 'us', 'uscpi', 'uscpicore', 'usfed',
       'usgas', 'usunemp', 'usnfp', 'fed', 'cut', 'hike', 'ecb', 'boj',
       'boe', 'boc', 'boi', 'bcb', 'cbr')
 GROUP BY 1 ORDER BY 2 DESC, 1;
