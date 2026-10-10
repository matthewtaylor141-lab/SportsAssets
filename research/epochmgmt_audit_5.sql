-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 5:
-- fixture normalization (PinnAPI exact-fixture misses), WNBA namespaced identities,
-- and the void terms the settlement-difference policy reads from each contract.
\echo == A rules coverage by venue and parse status: how many state a postponement window and payout
SELECT venue, parse_status, count(*) AS contracts,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'postponement_window_hours') AS with_window,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'postponement_payout') AS with_payout,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'void_rule') AS with_void_rule,
       count(*) FILTER (WHERE evidence ? 'verification_sources') AS with_sources
  FROM market_plane_rules GROUP BY 1, 2 ORDER BY 1, 2;

\echo == B contracts without a stated postponement window, by venue and series prefix (top 40)
SELECT venue,
       CASE WHEN venue = 'KALSHI' THEN split_part(contract_id, '-', 1)
            ELSE regexp_replace(contract_id, '-20[0-9][0-9]-.*$', '') END AS series_or_prefix,
       count(*) AS contracts
  FROM market_plane_rules
 WHERE NOT (evidence -> 'settlement' ? 'postponement_window_hours')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo == C PinnAPI exact-fixture misses in the last 6 hours by provider competition
SELECT sport_key, count(*) AS rows_n, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '6 hours'
   AND (first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE' OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%')
 GROUP BY 1 ORDER BY 3 DESC LIMIT 30;

\echo == D the home and away names of those misses (top 40 events)
SELECT sport_key, home, away, max(commence_time) AS commence, count(*) AS rows_n, max(us_market_slug) AS a_slug
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '6 hours'
   AND (first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE' OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%')
 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 40;

\echo == E WNBA provider rows in the last 14 days by outcome and first refusal
SELECT sport_key, outcome, first_refusal, count(*) AS rows_n, count(DISTINCT provider_event_id) AS events,
       min(cycle_at) AS first_at, max(cycle_at) AS last_at
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '14 days' AND (sport_key ILIKE '%wnba%' OR us_market_slug ILIKE '%wnba%')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo == F WNBA paper positions entered and their strategies
SELECT o.strategy, o.role, o.direction, count(*) AS orders, count(DISTINCT o.us_market_slug) AS markets,
       min(o.decided_at) AS first_at, max(o.decided_at) AS last_at
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.us_market_slug ILIKE '%wnba%'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == G the unique-participant check: WNBA city names that the NBA also uses, seen in the venue premap
SELECT team_league, team_name, side_norm, count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE event_slug ILIKE '%wnba%' OR team_league ILIKE '%wnba%'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
