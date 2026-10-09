-- RC6 lane Adriana (category 11, SHADOW), read only. The claim scan's
-- fixture read as the RC6 candidate spends its 80-fixture cap (cross-venue
-- first, then a readable Kalshi book, then start time): per venue, the
-- aliases it would read now and how many have a book inside the engine's
-- 30 s bound (what the recorded scan's books_fresh / markets_read read),
-- the mapped PMUS slugs without a premap identity, and the ESTABLISHED
-- fixture count in the (-4 h, +36 h) window at each hour, by start time,
-- to show when the cap binds.
\echo === A. aliases the capped read covers now, by venue and freshness ===
WITH fx AS (
  SELECT f.event_ticker, f.start_at, f.pmus_slug,
         coalesce(f.pmus_mapping_status = 'ESTABLISHED' AND f.pmus_slug IS NOT NULL, false) AS pm,
         coalesce(EXISTS (SELECT 1 FROM kalshi_books_current b
                           WHERE b.readable AND (b.ticker = ANY(f.team_tickers)
                                                 OR b.ticker = f.tie_ticker)), false) AS rd,
         f.team_tickers || CASE WHEN f.tie_ticker IS NULL THEN '{}'::text[]
                                ELSE ARRAY[f.tie_ticker] END AS tickers
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'),
kept AS (
  SELECT * FROM (SELECT fx.*, row_number() OVER (ORDER BY pm DESC, rd DESC, start_at, event_ticker) AS r
                   FROM fx) x
   WHERE r <= 80 AND rd),
k AS (
  SELECT 'KALSHI' AS venue,
         coalesce((SELECT b.observed_at > now() - interval '30 seconds'
                     FROM kalshi_books_current b WHERE b.ticker = t), false) AS fresh
    FROM kept, unnest(kept.tickers) AS t),
p AS (
  SELECT 'POLYMARKET_US' AS venue,
         EXISTS (SELECT 1 FROM paper_book_observations o
                  WHERE o.us_market_slug = kept.pmus_slug AND o.error IS NULL
                    AND o.observed_at > now() - interval '30 seconds') AS fresh
    FROM kept
   WHERE pm AND EXISTS (SELECT 1 FROM us_premap u WHERE u.market_slug = kept.pmus_slug))
SELECT venue, fresh, count(*) AS markets, 2 * count(*) AS aliases
  FROM (SELECT * FROM k UNION ALL SELECT * FROM p) x
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === B. mapped PMUS slugs in the window without a premap identity ===
SELECT count(*) AS mapped, count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM us_premap u WHERE u.market_slug = f.pmus_slug)) AS no_identity
  FROM kalshi_fixtures_current f
 WHERE f.mapping_status = 'ESTABLISHED' AND f.pmus_mapping_status = 'ESTABLISHED'
   AND f.pmus_slug IS NOT NULL
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours';
\echo === C. ESTABLISHED fixtures in the window at each hour (rows present now) ===
SELECT h AS at_hour,
       count(f.event_ticker) AS in_window,
       count(f.event_ticker) FILTER (WHERE f.pmus_mapping_status = 'ESTABLISHED') AS pmus_mapped
  FROM generate_series(date_trunc('hour', now()) - interval '12 hours',
                       date_trunc('hour', now()) + interval '24 hours',
                       interval '2 hours') AS h
  LEFT JOIN kalshi_fixtures_current f
    ON f.mapping_status = 'ESTABLISHED'
   AND f.start_at BETWEEN h - interval '4 hours' AND h + interval '36 hours'
 GROUP BY 1 ORDER BY 1;
