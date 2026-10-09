-- RC6.2 lane p-coverage (FIX), read only. Venue league codes the venue uses
-- for MORE THAN ONE sport. market_plane.ontology.LEAGUE_SPORT reads a sport
-- off the league code only for a row whose market type names no sport
-- (`futures`, `moneyline`, none); rc6_p-coverage_fix_evidence F1 showed
-- `pdc` (mapped darts) listing Chilean soccer games and `btla` / `vkl`
-- (mapped efootball) listing Moroccan / Finnish soccer games.
--   H1  every code seen (45 days) under two or more sports-type heads,
--       futures and untyped counted as their own heads, with events and one
--       event slug + title per head, JSON-packed
-- No write, no secret.
\echo === H1. codes under more than one sports-type head: [code, head, events, sample_slug, sample_title] ===
WITH s AS MATERIALIZED (
  SELECT split_part(event_slug, '-', 1) AS code,
         CASE WHEN sports_type IS NULL THEN '<none>'
              WHEN sports_type = 'futures' THEN 'futures'
              WHEN sports_type = 'moneyline' THEN 'moneyline'
              WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
              ELSE split_part(sports_type, '_', 1) END AS head,
         event_slug, max(event_title) AS title
    FROM us_premap
   WHERE event_slug IS NOT NULL
     AND updated_at > now() - interval '45 days'
   GROUP BY 1, 2, 3),
h AS (
  SELECT code, head, count(*) AS events, max(event_slug) AS sample_slug,
         max(left(title, 70)) AS sample_title
    FROM s GROUP BY 1, 2),
multi AS (
  SELECT code FROM h GROUP BY code HAVING count(*) > 1),
k AS (SELECT h.* FROM h JOIN multi USING (code)),
b AS (SELECT k.*, (dense_rank() OVER (ORDER BY code) - 1) / 8 AS grp FROM k)
SELECT json_agg(json_build_array(code, head, events, sample_slug,
                                 sample_title) ORDER BY code, head)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
