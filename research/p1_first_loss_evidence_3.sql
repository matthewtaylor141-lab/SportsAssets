-- READ-ONLY. P1 first-loss evidence, third read.
--
--   1. THE VENUE'S OWN SETTLEMENTS PER SPORT FAMILY / LEAGUE: how many settled
--      fixtures the venue paid ordinarily, settled at a price strictly
--      between 0 and 1 (the "last fair market price" branch: postponed /
--      suspended and not rescheduled), or declared void -- the measured basis
--      of the priced settlement-difference policy's postponement rate
--      (classification exactly settlement_exception_risk.classify_market's)
--   2. the paper ledger's settlements by outcome / family
--   3. the last cycle heartbeat's step timing and venue rate controls
--   4. which strategies wrote SETTLEMENT_NOT_SUPPORTED (24 h)
--
-- No writes. Every statement is a SELECT.

\echo == 1. venue settlements per family / league, fixture level (all history) ==
WITH m AS (
    SELECT us_market_slug AS slug,
           min(sport_family) AS fam,
           split_part(us_market_slug, '-', 2) AS lg,
           min(coalesce(event_key, us_market_slug)) AS fixture,
           bool_or(outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                     'VENUE_REPORTED_OUTCOME')) AS ord,
           bool_or(outcome_basis = 'CONFIRMED_VOID') AS void,
           bool_or(outcome_basis IS NULL
                   AND (CASE WHEN settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$'
                             THEN trim(settlement_read)::numeric END)
                       > 0
                   AND (CASE WHEN settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$'
                             THEN trim(settlement_read)::numeric END)
                       < 1) AS price
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
       AND us_market_slug IS NOT NULL
       AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
     GROUP BY us_market_slug),
f AS (
    SELECT fam, lg, fixture,
           bool_or(ord) AS ord, bool_or(void) AS void, bool_or(price) AS price
      FROM m GROUP BY 1, 2, 3)
SELECT fam, lg,
       count(*) AS fixtures_with_a_terminal_read,
       count(*) FILTER (WHERE (ord::int + void::int + price::int) = 1)
           AS non_conflicting,
       count(*) FILTER (WHERE price AND NOT ord AND NOT void) AS settled_at_price,
       count(*) FILTER (WHERE void AND NOT ord AND NOT price) AS declared_void,
       count(*) FILTER (WHERE ord AND NOT void AND NOT price) AS ordinary,
       count(*) FILTER (WHERE (ord::int + void::int + price::int) > 1)
           AS conflicting
  FROM f GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo == 1b. the fixtures settled at a price or void (all history) ==
WITH m AS (
    SELECT us_market_slug AS slug, min(sport_family) AS fam,
           min(coalesce(event_key, us_market_slug)) AS fixture,
           array_agg(DISTINCT outcome_basis) AS bases,
           array_agg(DISTINCT trim(settlement_read)) AS reads
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
       AND us_market_slug IS NOT NULL
       AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
     GROUP BY us_market_slug)
SELECT fam, slug, bases, reads FROM m
 WHERE 'CONFIRMED_VOID' = ANY(bases)
    OR EXISTS (SELECT 1 FROM unnest(reads) r
                WHERE (CASE WHEN r ~ '^[0-9]*\.[0-9]+$'
                            THEN r::numeric END) > 0
                  AND (CASE WHEN r ~ '^[0-9]*\.[0-9]+$'
                            THEN r::numeric END) < 1)
 ORDER BY 1, 2 LIMIT 80;

\echo == 2. paper settlements by outcome (latest version per position) ==
WITH s AS (
    SELECT DISTINCT ON (position_key) position_key, us_market_slug, outcome,
           payout_per_contract
      FROM paper_settlements ORDER BY position_key, version DESC)
SELECT split_part(us_market_slug, '-', 2) AS lg, outcome, count(*) AS positions,
       count(DISTINCT us_market_slug) AS markets
  FROM s GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 3. last cycle heartbeat: step timing, venue rate controls ==
SELECT value -> 'step_timing_s' AS step_timing_s
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT left((value -> 'venue_rate_controls')::text, 3000) AS venue_rate_controls
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT left((value -> 'paper_market_data')::text, 2000) AS paper_market_data
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT left((value -> 'funnel_by_provider_sport')::text, 4000) AS funnel
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo == 4. strategies writing SETTLEMENT_NOT_SUPPORTED (24 h) ==
SELECT d.policy_version, split_part(d.us_market_slug, '-', 2) AS lg,
       count(*) AS decisions
  FROM paper_decisions d
 WHERE d.decided_at >= now() - interval '24 hours'
   AND 'SETTLEMENT_NOT_SUPPORTED' = ANY(d.refusals)
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

SELECT d.policy_version, d.verdict, count(*) AS decisions
  FROM paper_decisions d
 WHERE d.decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 4b. settlement blockers on the valuations behind them (24 h, nba/mlb/unl/nhl/kbl) ==
SELECT v.sport_family, split_part(v.us_market_slug, '-', 2) AS lg,
       v.settlement_comparison -> 'blockers' AS blockers,
       v.settlement_comparison -> 'compatibility' AS compatibility,
       count(*) AS valuations
  FROM external_valuations v
 WHERE v.decided_at >= now() - interval '24 hours'
   AND v.record_purpose = 'ENTRY_DECISION'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;
