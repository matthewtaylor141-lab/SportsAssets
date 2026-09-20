-- BETTOR: WHY IS sport UNKNOWN, AND WHY DOES P_FILL HAVE ZERO ROWS.
--
-- Owner directive, "STOP here": §1 trace exactly why sport is UNKNOWN on
-- every row; §2 report an explicit funnel from raw observation to label
-- with a machine-readable reason on every lost row, and say which of
-- A/B/C/D/E the zero is.
--
-- READ-ONLY. Every statement is a SELECT.

-- ── 1. SPORT: what the observation rows actually carry ───────────────
SELECT 'SPORT_ON_OBSERVATIONS' AS section,
       count(*)                                          AS total_rows,
       count(*) FILTER (WHERE o.sport IS NOT NULL)        AS sport_not_null,
       count(*) FILTER (WHERE o.sport IS NULL)            AS sport_null,
       count(*) FILTER (WHERE o.league IS NOT NULL)       AS league_not_null,
       count(*) FILTER (WHERE o.league IS NULL)           AS league_null,
       count(DISTINCT o.sport)                            AS distinct_sports,
       count(DISTINCT o.league)                           AS distinct_leagues,
       min(o.observed_at)                                 AS first_observed,
       max(o.observed_at)                                 AS last_observed
  FROM bettor_opportunities o;

-- ── 2. SPORT: is the venue's own league string available upstream? ───
-- us_premap is the table universe() selects from. team_league is the
-- venue's team.league; sports_type is market.sportsMarketType. If these
-- are populated, the UNKNOWN is a plumbing gap, not missing venue data.
SELECT 'PREMAP_LEAGUE_AVAILABILITY' AS section,
       count(*)                                              AS premap_rows,
       count(*) FILTER (WHERE p.team_league IS NOT NULL)      AS team_league_present,
       count(*) FILTER (WHERE p.sports_type IS NOT NULL)      AS sports_type_present,
       count(*) FILTER (WHERE p.game_start IS NOT NULL)       AS game_start_present,
       count(DISTINCT p.team_league)                          AS distinct_team_leagues,
       count(*) FILTER (WHERE p.updated_at > now() - interval '2 hours')
           AS fresh_2h,
       count(*) FILTER (WHERE p.updated_at > now() - interval '2 hours'
                          AND p.team_league IS NOT NULL)
           AS fresh_2h_with_league
  FROM us_premap p;

-- ── 3. SPORT: the leagues the venue is actually stating, by volume ───
SELECT 'PREMAP_LEAGUES_OBSERVED' AS section,
       coalesce(p.team_league, '(null)') AS team_league,
       coalesce(p.sports_type, '(null)') AS sports_type,
       count(*)                          AS rows,
       count(*) FILTER (WHERE p.updated_at > now() - interval '2 hours')
           AS fresh_2h
  FROM us_premap p
 GROUP BY 1, 2, 3
 ORDER BY rows DESC
 LIMIT 60;

-- ── 4. SPORT: can an observation be joined back to a premap league? ──
-- This is the decisive number for SPORT_MAPPING_STATUS: how many
-- existing observation rows COULD be mapped from data already stored.
SELECT 'OBSERVATION_TO_PREMAP_JOIN' AS section,
       count(*)                                               AS observations,
       count(p.market_slug)                                   AS joined_to_premap,
       count(*) FILTER (WHERE p.team_league IS NOT NULL)       AS joined_with_league,
       count(*) FILTER (WHERE p.market_slug IS NULL)           AS no_premap_row,
       count(*) FILTER (WHERE p.market_slug IS NOT NULL
                          AND p.team_league IS NULL)           AS premap_row_no_league
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol;

-- ── 5. FUNNEL GATE 1: MARKET OBSERVED ────────────────────────────────
SELECT 'FUNNEL_1_MARKET_OBSERVED' AS section,
       count(*)                                    AS observations,
       count(DISTINCT o.symbol)                    AS distinct_markets,
       count(DISTINCT o.event_id)                  AS distinct_events,
       min(o.observed_at)                          AS first_at,
       max(o.observed_at)                          AS last_at,
       round(EXTRACT(EPOCH FROM (max(o.observed_at) - min(o.observed_at)))
             / 3600.0, 3)                          AS span_hours
  FROM bettor_opportunities o;

-- ── 6. FUNNEL GATE 2: ELIGIBLE MARKET (a readable, two-sided book) ───
SELECT 'FUNNEL_2_ELIGIBLE' AS section,
       count(*)                                                AS observations,
       count(*) FILTER (WHERE o.market_state_id IS NULL)
           AS lost_no_market_state,
       count(*) FILTER (WHERE s.market_state_id IS NOT NULL
                          AND s.readable IS NOT TRUE)
           AS lost_unreadable,
       count(*) FILTER (WHERE s.readable IS TRUE
                          AND (s.bid IS NULL) <> (s.ask IS NULL))
           AS lost_one_sided,
       count(*) FILTER (WHERE s.readable IS TRUE
                          AND s.bid IS NULL AND s.ask IS NULL)
           AS lost_no_prices,
       count(*) FILTER (WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL)
           AS eligible_two_sided,
       count(*) FILTER (WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
                          AND s.available_depth IS NOT NULL)
           AS eligible_with_depth
  FROM bettor_opportunities o
  LEFT JOIN shadow_market_states s ON s.market_state_id = o.market_state_id;

-- ── 7. FUNNEL GATE 3-4: HYPOTHETICAL ORDER / T0 SNAPSHOT ─────────────
-- These tables are where a prospective maker-order record would live.
-- If they do not exist the funnel stops here, and that is finding (B).
SELECT 'FUNNEL_3_T0_TABLES' AS section,
       t.table_name,
       (SELECT count(*) FROM information_schema.columns c
         WHERE c.table_name = t.table_name)  AS columns
  FROM information_schema.tables t
 WHERE t.table_schema = 'public'
   AND (t.table_name LIKE '%maker%'
     OR t.table_name LIKE '%hypothetical%'
     OR t.table_name LIKE '%p_fill%'
     OR t.table_name LIKE '%pfill%'
     OR t.table_name LIKE '%counterfactual%'
     OR t.table_name LIKE '%shadow_quote%'
     OR t.table_name LIKE '%bettor%')
 ORDER BY t.table_name;

-- ── 8. WHY EVERY DECISION REFUSES, BY REASON CODE ────────────────────
-- Unnests the blocker list so no row is collapsed into "it refused".
SELECT 'DECISION_BLOCKERS' AS section,
       b.value ->> 'code'  AS blocker_code,
       count(*)            AS occurrences,
       count(DISTINCT d.shadow_decision_id) AS decisions
  FROM shadow_decisions d
  CROSS JOIN LATERAL jsonb_array_elements(
       CASE WHEN jsonb_typeof(d.blockers) = 'array'
            THEN d.blockers ELSE '[]'::jsonb END) AS b(value)
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1, 2
 ORDER BY occurrences DESC
 LIMIT 40;

-- ── 9. OBSERVATION RATE: how fast evidence would accumulate ──────────
SELECT 'OBSERVATION_RATE' AS section,
       date_trunc('hour', o.observed_at) AS hour,
       count(*)                          AS observations,
       count(DISTINCT o.symbol)          AS distinct_markets
  FROM bettor_opportunities o
 WHERE o.observed_at > now() - interval '48 hours'
 GROUP BY 1, 2
 ORDER BY hour DESC
 LIMIT 48;

-- ── 10. IS CAPTURE EVEN RUNNING RIGHT NOW ────────────────────────────
SELECT 'CAPTURE_LIVENESS' AS section,
       (SELECT max(observed_at) FROM bettor_opportunities) AS last_opportunity,
       (SELECT round(EXTRACT(EPOCH FROM (now() - max(observed_at))))
          FROM bettor_opportunities)                       AS seconds_since,
       (SELECT max(updated_at) FROM us_premap)             AS last_premap,
       (SELECT max(captured_at) FROM shadow_market_states) AS last_market_state,
       (SELECT count(*) FROM shadow_decisions
         WHERE lane = 'BETTOR_EV_SHADOW')                  AS bettor_decisions,
       (SELECT max(decision_ts) FROM shadow_decisions
         WHERE lane = 'BETTOR_EV_SHADOW')                  AS last_decision,
       (SELECT count(*) FROM shadow_positions
         WHERE lane = 'BETTOR_EV_SHADOW')                  AS bettor_positions;
