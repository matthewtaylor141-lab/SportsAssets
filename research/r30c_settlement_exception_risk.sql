-- R30C SETTLEMENT-EXCEPTION RISK: THE MEASURED RECORD (read only).
--
-- Program section 15. Completed-game economics are CONDITIONAL on ordinary
-- completion (agents/paper_benchmark CG V3, ECONOMICS_LABEL
-- CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED). This reads what the venue
-- ACTUALLY settled, so the exceptional states can be counted instead of
-- assumed:
--
--   BINARY       outcome_basis VENUE_SETTLEMENT_PRICE / VENUE_REPORTED_OUTCOME
--                (the venue paid one side in full: ordinary settlement)
--   VOID         outcome_basis CONFIRMED_VOID (the venue DECLARED a void)
--   NONBINARY    outcome_basis NULL and a parseable settlement_read strictly
--                between 0 and 1: neither side was paid in full. Split into
--                exactly 0.5 (consistent with a tie payout) and any other
--                price (consistent with a last-fair-market-price settlement
--                of a postponed / suspended game). Never read as a refund.
--
-- EVENT LEVEL as well as market level: a fixture's postponement settles all
-- of its markets, so a fixture counts once (coalesce(event_key, slug)).
-- League = the venue slug's league token (split_part(slug, '-', 2), the
-- lane's own convention). Paper settlements are the HELD subset of the same
-- venue reads and are reported apart, never summed with them.
--
-- READ-ONLY: SELECTs only; no mutating keyword in executable SQL.

\echo == 1 == coverage: the settlement-read window
SELECT count(*) AS rows_with_a_read,
       count(DISTINCT us_market_slug) AS markets,
       count(DISTINCT coalesce(event_key, us_market_slug)) AS fixtures,
       min(settlement_read_at) AS first_read, max(settlement_read_at) AS last_read
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND us_market_slug IS NOT NULL
   AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL);

\echo == 2 == FIXTURE level by sport_family / league token: terminal classes
WITH r AS (
  SELECT sport_family, split_part(us_market_slug, '-', 2) AS league,
         us_market_slug, coalesce(event_key, us_market_slug) AS fixture,
         outcome_basis,
         CASE WHEN settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$'
              THEN trim(settlement_read)::numeric END AS sp
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND us_market_slug IS NOT NULL
     AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
), fx AS (
  SELECT sport_family, league, fixture,
         bool_or(outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                   'VENUE_REPORTED_OUTCOME')) AS any_binary,
         bool_or(outcome_basis = 'CONFIRMED_VOID') AS any_void,
         bool_or(outcome_basis IS NULL AND sp > 0 AND sp < 1) AS any_nonbinary,
         bool_or(outcome_basis IS NULL AND sp = 0.5) AS any_half,
         bool_or(outcome_basis IS NULL AND sp > 0 AND sp < 1 AND sp <> 0.5)
             AS any_other_price
    FROM r GROUP BY 1, 2, 3
)
SELECT sport_family, league,
       count(*) FILTER (WHERE any_binary OR any_void OR any_nonbinary)
           AS terminal_fixtures,
       count(*) FILTER (WHERE any_binary AND NOT any_void
                        AND NOT any_nonbinary) AS ordinary_only,
       count(*) FILTER (WHERE any_void) AS void_fixtures,
       count(*) FILTER (WHERE any_nonbinary) AS nonbinary_fixtures,
       count(*) FILTER (WHERE any_half) AS at_one_half,
       count(*) FILTER (WHERE any_other_price) AS other_price,
       count(*) FILTER (WHERE NOT (any_binary OR any_void OR any_nonbinary))
           AS read_but_not_terminal
  FROM fx GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2;

\echo == 3 == MARKET level by sport_family / league / market type
WITH r AS (
  SELECT sport_family, split_part(us_market_slug, '-', 2) AS league, market,
         us_market_slug, outcome_basis,
         CASE WHEN settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$'
              THEN trim(settlement_read)::numeric END AS sp
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND us_market_slug IS NOT NULL
     AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
), mk AS (
  SELECT sport_family, league, market, us_market_slug,
         bool_or(outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                   'VENUE_REPORTED_OUTCOME')) AS b,
         bool_or(outcome_basis = 'CONFIRMED_VOID') AS v,
         bool_or(outcome_basis IS NULL AND sp > 0 AND sp < 1) AS nb
    FROM r GROUP BY 1, 2, 3, 4
)
SELECT sport_family, league, market,
       count(*) FILTER (WHERE b OR v OR nb) AS terminal_markets,
       count(*) FILTER (WHERE v) AS void_markets,
       count(*) FILTER (WHERE nb) AS nonbinary_markets,
       count(*) FILTER (WHERE b AND (v OR nb)) AS conflicting_reads
  FROM mk GROUP BY 1, 2, 3 ORDER BY 4 DESC, 1, 2, 3;

\echo == 4 == every NONBINARY or VOID settlement, with the rule text it carried
WITH r AS (
  SELECT DISTINCT ON (us_market_slug)
         us_market_slug, sport_family, market, event_key, outcome_basis,
         settlement_read, settlement_read_at,
         (settlement_comparison->>'venue_rules_text') ILIKE
             '%last fair market price%' AS rules_state_lfmp,
         (settlement_comparison->>'venue_rules_text') ~* '\mtie\M'
             AS rules_mention_tie
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND us_market_slug IS NOT NULL
     AND (outcome_basis = 'CONFIRMED_VOID'
          OR (outcome_basis IS NULL
              AND settlement_read ~ '^\s*0?\.[0-9]*[1-9][0-9]*\s*$'))
   ORDER BY us_market_slug, settlement_read_at DESC NULLS LAST
)
SELECT * FROM r ORDER BY settlement_read_at DESC NULLS LAST LIMIT 150;

\echo == 5 == paper settlements (latest version per position) by strategy / league / outcome
WITH s AS (
  SELECT DISTINCT ON (position_key) position_key, group_id, us_market_slug,
         outcome, payout_per_contract, settled_at
    FROM paper_settlements ORDER BY position_key, version DESC
), g AS (
  SELECT DISTINCT ON (group_id) group_id, strategy, fixture
    FROM paper_orders WHERE role = 'ENTRY' ORDER BY group_id, decided_at
)
SELECT g.strategy, split_part(s.us_market_slug, '-', 2) AS league, s.outcome,
       count(*) AS positions, count(DISTINCT g.fixture) AS fixtures,
       min(s.settled_at) AS first, max(s.settled_at) AS last
  FROM s LEFT JOIN g USING (group_id)
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == 6 == every exceptional paper settlement (VOID_REFUND / SETTLED_AT_VENUE_PRICE)
SELECT DISTINCT ON (position_key) position_key, us_market_slug, holding_side,
       outcome, payout_per_contract, qty, settled_at,
       evidence->>'venue_long_price' AS venue_long_price,
       evidence->>'rule' AS rule
  FROM paper_settlements
 WHERE outcome IN ('VOID_REFUND', 'SETTLED_AT_VENUE_PRICE')
 ORDER BY position_key, version DESC LIMIT 100;

\echo == 7 == venue-vs-book RULE DIVERGENCE per exceptional condition (distinct markets, latest comparison)
WITH last AS (
  SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_family, market,
         split_part(us_market_slug, '-', 2) AS league,
         settlement_comparison->'per_condition' AS pc
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND us_market_slug IS NOT NULL
     AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
     AND decided_at > now() - interval '45 days'
   ORDER BY us_market_slug, decided_at DESC
)
SELECT sport_family, league, market, c.key AS condition,
       c.value->>'verdict' AS verdict,
       c.value->>'book_payout' AS book_payout,
       c.value->>'venue_payout' AS venue_payout,
       count(*) AS markets
  FROM last, jsonb_each(pc) c
 WHERE c.key NOT IN ('COMPLETED_IN_REGULATION', 'DECIDED_AFTER_REGULATION')
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 1, 2, 3, 4, 8 DESC;

\echo == 8 == markets compared per sport / league / market (the divergence denominator)
SELECT sport_family, split_part(us_market_slug, '-', 2) AS league, market,
       count(DISTINCT us_market_slug) AS markets_compared
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND us_market_slug IS NOT NULL
   AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
   AND decided_at > now() - interval '45 days'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo == 9 == pairing observations: the existing event-level void rate inputs
SELECT count(DISTINCT o.fixture) FILTER (WHERE l.label_status = 'LABELLED'
           OR (l.label_status = 'NOT_A_LABEL'
               AND l.label_why ILIKE 'the venue declared a void%'))
           AS settled_fixtures,
       count(DISTINCT o.fixture) FILTER (WHERE l.label_status = 'NOT_A_LABEL'
           AND l.label_why ILIKE 'the venue declared a void%') AS void_fixtures
  FROM bettor_pair_observation_labels l
  JOIN bettor_pair_observations o USING (observation_id);
