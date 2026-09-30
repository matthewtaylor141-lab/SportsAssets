-- READ-ONLY. The exact catalogue rows (us_premap) of a few upcoming fixtures
-- in the captured families, so the pair supplier's handling of each real row
-- shape (soccer three-outcome winners, MLB winner + spread) can be reproduced
-- offline. Catalogue metadata only; no account, balance or credential field.

\echo '== R1 · fixtures chosen: soccer winner-only, soccer multi-kind, MLB spread+winner =='
WITH win AS (
  SELECT * FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL
     AND split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer')),
fx AS (
  SELECT event_slug, split_part(min(sports_type), '_', 1) AS family,
         count(DISTINCT sports_type) AS kinds, count(DISTINCT market_slug) AS contracts
    FROM win GROUP BY 1),
chosen AS (
  (SELECT event_slug FROM fx WHERE family = 'soccer' AND kinds = 1 ORDER BY event_slug LIMIT 1)
  UNION ALL
  (SELECT event_slug FROM fx WHERE family = 'soccer' AND kinds >= 4 ORDER BY event_slug LIMIT 1)
  UNION ALL
  (SELECT event_slug FROM fx WHERE family = 'baseball' AND kinds >= 2 ORDER BY event_slug LIMIT 1))
SELECT w.event_slug, w.event_title, w.market_slug, w.intent, w.sports_type,
       w.team_abbr, w.side_norm, w.signed, w.line, w.kind, w.question, w.game_start
  FROM win w JOIN chosen c USING (event_slug)
 WHERE w.sports_type ~ '(_spread|_winner|_total_points)$'
 ORDER BY w.event_slug, w.sports_type, w.market_slug, w.intent
 LIMIT 120;
