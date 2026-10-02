-- Team-logo inventory (read-only): every league and team that appears in the
-- paper account's opportunities (decisions, 7 d), orders and open positions,
-- resolved through the venue's own catalogue (us_premap team fields), with
-- BOTH sides of each event (all catalogue rows sharing the event slug).
\echo '== L0 · slugs touched and how many resolve to a catalogue row =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug, 'decision' AS src FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug, 'order' FROM paper_orders WHERE account_id = 'paper_acct_main'
)
SELECT src, count(DISTINCT slug) AS slugs,
       count(DISTINCT slug) FILTER (WHERE EXISTS (SELECT 1 FROM us_premap p WHERE p.market_slug = t.slug)) AS in_catalogue
  FROM touched t GROUP BY 1;

\echo '== L1 · teams by sport/league across the touched events (both sides) =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug FROM paper_orders WHERE account_id = 'paper_acct_main'
), ev AS (
  SELECT DISTINCT p.event_slug FROM us_premap p JOIN touched t ON t.slug = p.market_slug
)
SELECT coalesce(p.sports_type, '?') AS sports_type, coalesce(p.team_league, '?') AS league,
       count(DISTINCT p.event_slug) AS events,
       count(DISTINCT coalesce(p.team_id::text, 'name:' || p.team_name)) AS teams,
       count(DISTINCT p.team_id) AS teams_with_id,
       count(*) FILTER (WHERE p.team_name IS NULL AND p.team_id IS NULL) AS rows_without_team
  FROM us_premap p JOIN ev USING (event_slug)
 GROUP BY 1, 2 ORDER BY events DESC;

\echo '== L2 · every distinct team (id, league, venue names) =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug FROM paper_orders WHERE account_id = 'paper_acct_main'
), ev AS (
  SELECT DISTINCT p.event_slug FROM us_premap p JOIN touched t ON t.slug = p.market_slug
)
SELECT p.sports_type, p.team_league, p.team_id, p.team_abbr, p.team_name, p.team_safe_name,
       count(DISTINCT p.event_slug) AS events, min(p.event_slug) AS example_event
  FROM us_premap p JOIN ev USING (event_slug)
 WHERE p.team_id IS NOT NULL OR p.team_name IS NOT NULL
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 5 LIMIT 400;

\echo '== L3 · touched slugs with no catalogue row (by slug prefix) =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug FROM paper_orders WHERE account_id = 'paper_acct_main'
)
SELECT split_part(slug, '-', 1) || '-' || split_part(slug, '-', 2) AS prefix, count(*) AS n, min(slug) AS example
  FROM touched t WHERE NOT EXISTS (SELECT 1 FROM us_premap p WHERE p.market_slug = t.slug)
 GROUP BY 1 ORDER BY n DESC;
