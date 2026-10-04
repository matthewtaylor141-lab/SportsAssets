-- P0 INCIDENT (2026-10-04) -- AGENT DELIVERY, part 3: the per-league delivery
-- funnel from the VENUE'S own full-game winner events to the agents
-- (valued -> decided by CG V3 -> ENTER -> ordered -> filled), and the paper
-- pass budget history. READ-ONLY; bounded windows; LIMITs on every dump.

\echo == L1 venue full-game winner events, game_start now-24h .. now+12h, by sport type x league token, and how many reached the agents ==
WITH ev AS (
  SELECT DISTINCT p.event_slug, p.sports_type,
         lower(coalesce(nullif(p.team_league, ''),
                        split_part(p.event_slug, '-', 2))) AS league,
         p.game_start
    FROM us_premap p
   WHERE p.game_start >= now() - interval '24 hours'
     AND p.game_start <  now() + interval '12 hours'
     AND p.sports_type IN ('soccer_team_full_time_winner',
                           'baseball_team_full_game_winner',
                           'football_team_full_game_winner',
                           'basketball_team_full_game_winner',
                           'hockey_team_full_game_winner',
                           'icehockey_team_full_game_winner',
                           'tennis_match_winner')),
mk AS (
  SELECT DISTINCT p.event_slug, coalesce(p.identifier, p.market_slug) AS slug,
         p.market_slug
    FROM us_premap p JOIN ev USING (event_slug)
   WHERE p.sports_type = ev.sports_type),
val AS (
  SELECT DISTINCT mk.event_slug
    FROM external_valuations v
    JOIN mk ON v.us_market_slug IN (mk.slug, mk.market_slug)
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.decided_at > now() - interval '36 hours'),
dec AS (
  SELECT mk.event_slug,
         bool_or(d.verdict = 'ENTER') AS entered,
         bool_or(r.code = 'BELOW_MIN_GROSS_EDGE') AS econ_refused
    FROM paper_decisions d
    JOIN mk ON d.us_market_slug IN (mk.slug, mk.market_slug)
    LEFT JOIN LATERAL unnest(d.refusals) AS r(code) ON true
   WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
     AND d.decided_at > now() - interval '36 hours'
   GROUP BY 1),
ord AS (
  SELECT mk.event_slug, bool_or(o.state = 'FILLED' OR o.filled_qty > 0) AS filled
    FROM paper_orders o
    JOIN mk ON o.us_market_slug IN (mk.slug, mk.market_slug)
   WHERE o.role = 'ENTRY' AND o.created_at > now() - interval '36 hours'
   GROUP BY 1)
SELECT ev.sports_type, ev.league,
       count(*) AS venue_events,
       count(val.event_slug) AS valued,
       count(dec.event_slug) AS cg_decided,
       count(*) FILTER (WHERE dec.econ_refused) AS cg_econ_refused_any,
       count(*) FILTER (WHERE dec.entered) AS cg_entered,
       count(ord.event_slug) AS ordered_any_strategy,
       count(*) FILTER (WHERE ord.filled) AS filled_any_strategy
  FROM ev
  LEFT JOIN val USING (event_slug)
  LEFT JOIN dec USING (event_slug)
  LEFT JOIN ord USING (event_slug)
 GROUP BY 1, 2
 ORDER BY venue_events DESC
 LIMIT 120;

\echo == L2 the same, rolled up by sport type ==
WITH ev AS (
  SELECT DISTINCT p.event_slug, p.sports_type
    FROM us_premap p
   WHERE p.game_start >= now() - interval '24 hours'
     AND p.game_start <  now() + interval '12 hours'
     AND p.sports_type IN ('soccer_team_full_time_winner',
                           'baseball_team_full_game_winner',
                           'football_team_full_game_winner',
                           'basketball_team_full_game_winner',
                           'hockey_team_full_game_winner',
                           'icehockey_team_full_game_winner',
                           'tennis_match_winner')),
mk AS (
  SELECT DISTINCT p.event_slug, coalesce(p.identifier, p.market_slug) AS slug,
         p.market_slug
    FROM us_premap p JOIN ev USING (event_slug)
   WHERE p.sports_type = ev.sports_type),
val AS (
  SELECT DISTINCT mk.event_slug
    FROM external_valuations v
    JOIN mk ON v.us_market_slug IN (mk.slug, mk.market_slug)
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.decided_at > now() - interval '36 hours')
SELECT ev.sports_type, count(*) AS venue_events, count(val.event_slug) AS valued
  FROM ev LEFT JOIN val USING (event_slug)
 GROUP BY 1 ORDER BY 2 DESC
 LIMIT 20;

\echo == L3 paper pass budget history (recent heartbeats kept on the session health row) ==
WITH h AS (
  SELECT jsonb_array_elements(recent_heartbeats) AS b
    FROM paper_session_health
   WHERE session_id = 'paper_session_20261001T014716Z')
SELECT count(*) AS passes_kept,
       min(to_timestamp((b->>'at')::float8)) AS first_at,
       max(to_timestamp((b->>'at')::float8)) AS last_at,
       count(*) FILTER (WHERE (b->'summary'->>'budget_exhausted')::boolean) AS budget_exhausted,
       round(avg((b->>'elapsed_s')::float8)::numeric, 2) AS avg_elapsed_s,
       round(max((b->>'elapsed_s')::float8)::numeric, 2) AS max_elapsed_s,
       round(avg((b->'summary'->>'books_read')::float8)::numeric, 2) AS avg_books_read,
       round(avg((b->'summary'->>'reviews')::float8)::numeric, 2) AS avg_reviews,
       count(*) FILTER (WHERE NOT (b->>'ok')::boolean) AS passes_with_error
  FROM h;

SELECT passes, errors, heartbeat_at, left(coalesce(last_error, ''), 300) AS last_error
  FROM paper_session_health
 WHERE session_id = 'paper_session_20261001T014716Z';

\echo == L4 the trigger mix of paper passes is not stored per pass; the paper heartbeats per trigger in the last pass ==
SELECT value->>'trigger' AS trigger, value->>'refusal' AS refusal, value->>'why' AS why,
       value->>'elapsed_s' AS elapsed_s, value->'steps'->'books' AS books,
       value->'steps'->'simulate_after_delay' AS after_delay
  FROM ingestion_state
 WHERE key = 'paper_session_last_pass';

\echo == L5 entry orders (24 h): seconds from decision to the first READABLE book, and to the first observation of any kind ==
WITH o AS (
  SELECT order_id, strategy, us_market_slug, state, terminal_reason,
         decided_at, eligible_at, expires_at
    FROM paper_orders
   WHERE role = 'ENTRY' AND created_at > now() - interval '24 hours')
SELECT o.strategy, o.state, coalesce(o.terminal_reason, '-') AS reason,
       count(*) AS orders,
       round(avg(extract(epoch FROM (
         (SELECT min(p.observed_at) FROM paper_book_observations p
           WHERE p.us_market_slug = o.us_market_slug
             AND p.observed_at >= o.eligible_at AND p.error IS NULL)
         - o.eligible_at)))::numeric, 1) AS avg_s_to_first_readable_any_time,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM paper_book_observations p
          WHERE p.us_market_slug = o.us_market_slug AND p.error IS NULL
            AND p.observed_at > o.expires_at
            AND p.observed_at <= o.expires_at + interval '10 minutes')) AS readable_within_10min_after_expiry
  FROM o
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 40;
