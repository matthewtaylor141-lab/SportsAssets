-- One venue market slug per touched team id (read-only): the market whose
-- side carries that team object, used to read the venue's own team record.
\echo '== TS · team id -> market slug =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug FROM paper_orders WHERE account_id = 'paper_acct_main'
), ev AS (SELECT DISTINCT p.event_slug FROM us_premap p JOIN touched t ON t.slug = p.market_slug)
SELECT DISTINCT ON (p.team_id) p.team_id, p.team_league, p.market_slug
  FROM us_premap p JOIN ev USING (event_slug)
 WHERE p.team_id IS NOT NULL
 ORDER BY p.team_id, (p.sports_type LIKE '%full_time_winner' OR p.sports_type LIKE '%full_game_moneyline') DESC, p.market_slug;
\echo '== TS2 · comma list =='
WITH touched AS (
  SELECT DISTINCT us_market_slug AS slug FROM paper_decisions
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days' AND us_market_slug IS NOT NULL
  UNION SELECT DISTINCT us_market_slug FROM paper_orders WHERE account_id = 'paper_acct_main'
), ev AS (SELECT DISTINCT p.event_slug FROM us_premap p JOIN touched t ON t.slug = p.market_slug),
pick AS (SELECT DISTINCT ON (p.team_id) p.market_slug FROM us_premap p JOIN ev USING (event_slug)
          WHERE p.team_id IS NOT NULL
          ORDER BY p.team_id, (p.sports_type LIKE '%full_time_winner' OR p.sports_type LIKE '%full_game_moneyline') DESC, p.market_slug)
SELECT string_agg(DISTINCT market_slug, ',') FROM pick;
