-- RC6 lane Adriana (category 11, SHADOW), read only. The claim-first scan's
-- universe at one instant, as canonical_claims_db.assemble reads it: every
-- ESTABLISHED Kalshi fixture in (-4 h, +36 h) with a readable Kalshi book;
-- per alias (YES and NO of each team / tie ticker, and of the mapped PMUS
-- moneyline) whether its book is inside the engine's 30 s bound now. This is
-- what the recorded claim scan's books_fresh / markets_read will read.
\echo === A. aliases by venue and book state at this instant ===
WITH fx AS (
  SELECT f.event_ticker, f.pmus_slug, f.pmus_mapping_status,
         f.team_tickers || CASE WHEN f.tie_ticker IS NULL THEN '{}'::text[]
                                ELSE ARRAY[f.tie_ticker] END AS tickers
    FROM kalshi_fixtures_current f
   WHERE f.mapping_status = 'ESTABLISHED'
     AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'),
readable AS (
  SELECT fx.* FROM fx
   WHERE EXISTS (SELECT 1 FROM kalshi_books_current b
                  WHERE b.ticker = ANY(fx.tickers) AND b.readable)),
k AS (
  SELECT 'KALSHI' AS venue, t AS market,
         (SELECT b.readable AND b.observed_at > now() - interval '30 seconds'
            FROM kalshi_books_current b WHERE b.ticker = t) AS fresh,
         (SELECT b.book_basis FROM kalshi_books_current b WHERE b.ticker = t) AS basis
    FROM readable, unnest(readable.tickers) AS t),
p AS (
  SELECT 'POLYMARKET_US' AS venue, r.pmus_slug AS market,
         EXISTS (SELECT 1 FROM paper_book_observations o
                  WHERE o.us_market_slug = r.pmus_slug AND o.error IS NULL
                    AND o.observed_at > now() - interval '30 seconds') AS fresh,
         'paper_book_observations'::text AS basis
    FROM readable r WHERE r.pmus_mapping_status = 'ESTABLISHED' AND r.pmus_slug IS NOT NULL)
SELECT venue, coalesce(basis, '<no book>') AS basis, coalesce(fresh, false) AS fresh,
       count(*) AS markets, 2 * count(*) AS aliases
  FROM (SELECT * FROM k UNION ALL SELECT * FROM p) x
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo === B. readable fixtures and the share of them mapped to PMUS ===
SELECT count(*) AS readable_fixtures,
       count(*) FILTER (WHERE pmus_mapping_status = 'ESTABLISHED') AS pmus_mapped
  FROM kalshi_fixtures_current f
 WHERE f.mapping_status = 'ESTABLISHED'
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
   AND EXISTS (SELECT 1 FROM kalshi_books_current b
                WHERE b.ticker = ANY(f.team_tickers) AND b.readable);
