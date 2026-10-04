-- P0 INCIDENT, segment MARKET-FAMILY / SETTLEMENT / PROBABILITY / FRESHNESS,
-- second read. READ-ONLY.
--
-- G1  every NFL / CFB valuation row of the last 48 h: provider, clocks, the
--     PinnAPI fallback reason, refusals.
-- G2  what the completed-game check `probability_qualified_by_the_lane` hides:
--     the lane codes it carried, by family x league (last 24 h).
-- G3  the venue's NFL board for the 2026-10-04 America/New_York day, by market
--     family (sports_type): contracts and events.
-- G4  the collector ledger per NFL event on 2026-10-04 (ET day): rows by first
--     refusal, so stale-on-arrival and no-Pinnacle are counted per game.
-- G5  full-game spread / total lines on the venue by league: integer (a push is
--     possible) vs half-point (no push), [now-6h, now+48h].
-- G6  WINNER contracts by sport x league x phase, [now-6h, now+48h]: the
--     ordinary moneyline universe the venue lists.
\echo '== G0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== G1 · NFL / CFB valuation rows, last 48 h =='
SELECT v.id, v.decided_at, v.us_market_slug, v.provider, v.outcomes_priced, v.expected_outcomes,
       v.probability IS NOT NULL AS has_p, v.observed_at, v.received_at,
       coalesce(v.settlement_comparison->'reference_input'->>'fallback_reason', '-') AS pinnapi_fallback,
       array_to_string(v.refusals[1:6], ',') AS first_refusals
  FROM external_valuations v
 WHERE v.sport_family = 'football' AND v.decided_at > now() - interval '48 hours'
 ORDER BY v.decided_at LIMIT 60;

\echo '== G2 · lane codes behind probability_qualified_by_the_lane, last 24 h =='
SELECT d.strategy, v.sport_family, lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) AS league,
       lr.code AS lane_code, count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id,
       LATERAL jsonb_array_elements(
         CASE WHEN jsonb_typeof(d.pinnacle->'contract_match'->'checks') = 'array'
              THEN d.pinnacle->'contract_match'->'checks' ELSE '[]'::jsonb END) AS c(x),
       LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(x->'lane_refusals') = 'array' THEN x->'lane_refusals'
              ELSE '[]'::jsonb END) AS lr(code)
 WHERE d.decided_at > now() - interval '24 hours'
   AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND x->>'check' = 'probability_qualified_by_the_lane'
   AND (x->>'passed')::boolean IS NOT TRUE
 GROUP BY 1, 2, 3, 4 ORDER BY 2, 3, 5 DESC LIMIT 60;

\echo '== G2b · the same decisions: lane codes recorded as not blocking (incl. MARKET_NOT_IN_SUPPORTED_SET) =='
SELECT v.sport_family, lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) AS league,
       nb.code AS not_blocking_code, count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id,
       LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(d.pinnacle->'contract_match'->'lane_refusals_not_blocking') = 'array'
              THEN d.pinnacle->'contract_match'->'lane_refusals_not_blocking' ELSE '[]'::jsonb END) AS nb(code)
 WHERE d.decided_at > now() - interval '24 hours'
   AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND v.sport_family = 'football'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC LIMIT 40;

\echo '== G3 · venue NFL board, 2026-10-04 ET day, by sports_type =='
SELECT sports_type, split_part(market_slug, '-', 1) AS slug_prefix,
       count(DISTINCT market_slug) AS contracts, count(DISTINCT event_slug) AS events,
       count(*) FILTER (WHERE line IS NOT NULL AND line <> '') AS rows_with_line
  FROM us_premap
 WHERE lower(split_part(market_slug, '-', 2)) = 'nfl'
   AND game_start >= timestamptz '2026-10-04 04:00Z' AND game_start < timestamptz '2026-10-05 04:00Z'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 90;

\echo '== G3b · venue CFB board, 2026-10-03 ET day, by family class =='
SELECT CASE
         WHEN sports_type LIKE '%\_player\_%' THEN 'PLAYER_PROP'
         WHEN sports_type ~ '(first_five|inning|half|quarter|_period|_set)' THEN 'PERIOD'
         WHEN sports_type ~ '(spread|handicap)' THEN 'SPREAD'
         WHEN sports_type ~ 'team_total' THEN 'TEAM_TOTAL'
         WHEN sports_type ~ 'total' THEN 'TOTAL'
         WHEN sports_type ~ '_winner$' THEN 'WINNER'
         ELSE 'OTHER:' || coalesce(sports_type, '<null>') END AS family_class,
       count(DISTINCT market_slug) AS contracts, count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE lower(split_part(market_slug, '-', 2)) = 'cfb'
   AND game_start >= timestamptz '2026-10-03 04:00Z' AND game_start < timestamptz '2026-10-04 04:00Z'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo '== G4 · collector ledger per NFL event, 2026-10-04 ET day =='
SELECT home || ' @ ' || away AS fixture_as_listed, min(commence_time) AS commence,
       count(*) AS ledger_rows,
       count(*) FILTER (WHERE first_refusal = 'NO_PINNACLE_ON_EVENT') AS no_pinnacle,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_on_arrival,
       count(*) FILTER (WHERE first_refusal = 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED') AS venue_currency,
       count(*) FILTER (WHERE first_refusal LIKE 'VENUE_BOOK_READ%') AS venue_read_failed,
       count(*) FILTER (WHERE first_refusal NOT IN ('NO_PINNACLE_ON_EVENT', 'QUOTE_STALE_ON_ARRIVAL',
                               'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED') AND first_refusal NOT LIKE 'VENUE_BOOK_READ%') AS other,
       max(us_market_slug) AS slug
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_nfl'
   AND cycle_at >= timestamptz '2026-10-03 12:00Z'
   AND commence_time >= '2026-10-04T04:00' AND commence_time < '2026-10-05T04:00'
 GROUP BY 1 ORDER BY 2 LIMIT 40;

\echo '== G5 · full-game spread / total lines: integer (push possible) vs half-point, [now-6h, now+48h] =='
SELECT sports_type, lower(split_part(market_slug, '-', 2)) AS league,
       count(DISTINCT market_slug) AS contracts,
       count(DISTINCT market_slug) FILTER (WHERE line ~ '^-?[0-9]+(\.0+)?$') AS integer_line_contracts,
       count(DISTINCT market_slug) FILTER (WHERE line ~ '^-?[0-9]+\.5$') AS half_line_contracts,
       count(DISTINCT market_slug) FILTER (WHERE line !~ '^-?[0-9]+(\.[0-9]+)?$' OR line IS NULL) AS unparsed_line,
       min(line) AS line_example
  FROM us_premap
 WHERE game_start > now() - interval '6 hours' AND game_start < now() + interval '48 hours'
   AND sports_type ~ '_full_game_(spread|total)$|_full_time_(spread|total)$|team_full_game_spread|full_game_total|match_games_spread|match_total_games|match_sets_spread'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo '== G6 · WINNER contracts by sports_type x league, [now-6h, now+48h], pre-game vs started =='
SELECT sports_type, lower(split_part(market_slug, '-', 2)) AS league,
       count(DISTINCT market_slug) FILTER (WHERE game_start > now()) AS contracts_pre_game,
       count(DISTINCT market_slug) FILTER (WHERE game_start <= now()) AS contracts_started,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE game_start > now() - interval '6 hours' AND game_start < now() + interval '48 hours'
   AND sports_type ~ '(full_game_winner|full_time_winner|match_winner)$'
 GROUP BY 1, 2 ORDER BY 3 + 4 DESC LIMIT 60;

\echo '== G7 · NFL decisions per strategy on 2026-10-04: verdict, every refusal =='
SELECT d.strategy, d.verdict, array_to_string(d.refusals, ',') AS refusals,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets,
       min(d.decided_at) AS first_at, max(d.decided_at) AS last_at
  FROM paper_decisions d
 WHERE lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) IN ('nfl', 'cfb')
   AND d.decided_at > now() - interval '48 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 30;

\echo '== G8 · NFL / CFB paper orders and fills, last 7 days =='
SELECT 'orders' AS what, count(*) AS n FROM paper_orders
 WHERE lower(split_part(coalesce(us_market_slug, ''), '-', 2)) IN ('nfl', 'cfb')
   AND created_at > now() - interval '7 days'
UNION ALL
SELECT 'fills', count(*) FROM paper_fills
 WHERE lower(split_part(coalesce(us_market_slug, ''), '-', 2)) IN ('nfl', 'cfb')
   AND filled_at > now() - interval '7 days';
