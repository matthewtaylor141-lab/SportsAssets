-- RC6 lane Adriana (category 11, SHADOW), read only. Which Kalshi series the
-- claim scan's ESTABLISHED game fixtures come from (a period / half series
-- is not a game moneyline), and the full Kalshi soccer game text whose
-- postponement window the registry does not read.
\echo === A. kalshi_fixtures_current ESTABLISHED in the claim window, by series ===
SELECT split_part(event_ticker, '-', 1) AS series, league, outcome_kind,
       coalesce(pmus_mapping_status, '<null>') AS pmus, count(*) AS n
  FROM kalshi_fixtures_current
 WHERE mapping_status = 'ESTABLISHED'
   AND start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 60;
\echo === B. one full Kalshi soccer game rules block and its registry parse ===
SELECT r.contract_id, r.parse_status, (r.evidence->'settlement')::text AS settlement,
       regexp_replace(coalesce(r.rules_text, ''), '\s+', ' ', 'g') AS primary_text,
       regexp_replace(coalesce(r.rules_secondary, ''), '\s+', ' ', 'g') AS secondary_text
  FROM market_plane_rules r
 WHERE r.venue = 'KALSHI'
   AND (r.contract_id LIKE 'kalshi:KXEPLGAME-%' OR r.contract_id LIKE 'kalshi:KXBUNDESLIGA1H-%')
 ORDER BY r.observed_at DESC LIMIT 2;
\echo === C. the claim scan's PMUS slugs: newest recorded book of each, any age ===
SELECT f.pmus_slug,
       (SELECT max(o.observed_at) FROM paper_book_observations o
         WHERE o.us_market_slug = f.pmus_slug) AS newest_book,
       (SELECT count(*) FROM paper_book_observations o
         WHERE o.us_market_slug = f.pmus_slug
           AND o.observed_at > now() - interval '24 hours') AS books_24h
  FROM kalshi_fixtures_current f
 WHERE f.pmus_mapping_status = 'ESTABLISHED'
   AND f.start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 ORDER BY 2 DESC NULLS LAST LIMIT 40;
