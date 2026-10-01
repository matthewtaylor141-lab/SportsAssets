-- READ-ONLY. DEREK RESEARCH BOOTSTRAP: WHAT THE BACKFILL WILL FIND.
--
-- Runs against the CURRENT production schema (external_valuations only), so
-- it can answer before the release that adds derek_research_observations is
-- migrated. It applies the observer's own rules in SQL:
--   derek_research.observation_from_row  (mode BACKFILL)  -> which stored
--     valuations become research observations, and the named reason for each
--     one that does not;
--   derek_research.LABEL_SQL             -> which observations carry a label.
-- Counted as the fit counts them: DISTINCT fixtures (condition:, else event:,
-- else slug:  -- derek_policy.fixture_of) per cohort, against
-- bettor_funded_model.MIN_TRAIN_EVENTS = 40.
--
-- Cross-checked 2026-10-01 on a scratch copy of the ddd4050 schema with 85
-- SYNTHETIC valuations covering every observer refusal and label state: P2's
-- OBSERVABLE counts, all six refusal counts, and P3's labelled rows and
-- fixtures per cohort equal what the real DR.backfill recorded and
-- DR.labelled_observations returned.
--
-- This is a PREVIEW computed from the same stored fields. The authoritative
-- count is the one the app records once the backfill has run
-- (research/derek_research_bootstrap_readback.sql). Backfilled rows train;
-- they never count as prospective evaluation evidence or toward promotion.
--
-- P1 stored valuations of the entry experiment, by purpose / cohort
-- P2 observer outcome per row (OBSERVABLE, or the observer's refusal name)
-- P3 labelled fixtures per cohort vs 40  <- the eligible training count
-- P4 observable but not labelled, by reason
-- P5 how the backfill walks: steps of 2000 newest-first, and where the
--    labelled fixtures sit (what the FIRST step alone would see)
-- P6 labelled stored valuations by settlement day (recency; upper bound)

\echo '== P1 · stored valuations of EXT_PINNACLE_DEVIG_V1_SHADOW =='
SELECT record_purpose,
       CASE record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                           WHEN 'ENTRY_DECISION'   THEN 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
       count(*) AS valuations, min(decided_at) AS oldest, max(decided_at) AS newest,
       min(id) AS min_id, max(id) AS max_id
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
 GROUP BY 1, 2 ORDER BY 1;

\echo '== P2 · observer outcome per stored valuation (BACKFILL rules, in the observer''s order) =='
WITH v AS (
  SELECT id, record_purpose, decided_at, observed_at, probability,
         outcome_known, outcome, outcome_basis, outcome_at,
         CASE WHEN NULLIF(condition_id, '') IS NOT NULL THEN 'condition:' || condition_id
              WHEN NULLIF(event_key, '')    IS NOT NULL THEN 'event:' || event_key
              WHEN NULLIF(us_market_slug, '') IS NOT NULL THEN 'slug:' || us_market_slug END AS fixture,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN COALESCE(
                calibration_only_evidence #>> '{compared_at_the_displayed_price,price}',
                calibration_only_evidence #>> '{displayed_quote,acquisition_price}')
           WHEN 'ENTRY_DECISION' THEN executable_price::text END AS price_txt,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN calibration_only_evidence #>> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION' THEN risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_txt
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), n AS (
  SELECT v.*,
         CASE WHEN price_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
              THEN price_txt::double precision END AS price,
         CASE WHEN receipt_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
              THEN receipt_txt::double precision END AS receipt
    FROM v
), r AS (
  SELECT n.*,
         CASE WHEN fixture IS NULL                 THEN 'NO_FIXTURE'
              WHEN price IS NULL                   THEN 'NO_PRICE'
              WHEN NOT (price > 0 AND price < 1)   THEN 'PRICE_OUT_OF_RANGE'
              WHEN probability IS NULL             THEN 'NO_PINNACLE'
              WHEN decided_at IS NULL              THEN 'BACKFILL_NO_DECISION_TIME'
              WHEN receipt IS NULL                 THEN 'BACKFILL_NO_PRICE_RECEIPT'
              WHEN observed_at IS NULL             THEN 'BACKFILL_NO_PINNACLE_STAMP'
              ELSE 'OBSERVABLE' END AS observer,
         (outcome_known AND outcome IN (0, 1)
          AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
          AND outcome_at IS NOT NULL) AS labelled
    FROM n
)
SELECT record_purpose, observer, count(*) AS valuations,
       count(DISTINCT fixture) AS distinct_fixtures,
       count(*) FILTER (WHERE labelled) AS labelled_rows,
       count(DISTINCT fixture) FILTER (WHERE labelled) AS labelled_fixtures
  FROM r GROUP BY 1, 2 ORDER BY 1, (observer <> 'OBSERVABLE'), 2;

\echo '== P3 · ELIGIBLE TRAINING EVIDENCE: labelled observable fixtures per cohort vs MIN_TRAIN_EVENTS = 40 =='
WITH r AS (
  SELECT id, record_purpose, outcome,
         CASE WHEN NULLIF(condition_id, '') IS NOT NULL THEN 'condition:' || condition_id
              WHEN NULLIF(event_key, '')    IS NOT NULL THEN 'event:' || event_key
              WHEN NULLIF(us_market_slug, '') IS NOT NULL THEN 'slug:' || us_market_slug END AS fixture,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN COALESCE(
                calibration_only_evidence #>> '{compared_at_the_displayed_price,price}',
                calibration_only_evidence #>> '{displayed_quote,acquisition_price}')
           WHEN 'ENTRY_DECISION' THEN executable_price::text END AS price_txt,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN calibration_only_evidence #>> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION' THEN risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_txt,
         probability, decided_at, observed_at,
         (outcome_known AND outcome IN (0, 1)
          AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
          AND outcome_at IS NOT NULL) AS labelled
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), ok AS (
  SELECT * FROM r
   WHERE fixture IS NOT NULL
     AND price_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
     AND price_txt::double precision > 0 AND price_txt::double precision < 1
     AND probability IS NOT NULL AND decided_at IS NOT NULL AND observed_at IS NOT NULL
     AND receipt_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
     AND labelled
)
SELECT CASE record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                           ELSE 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
       count(*) AS labelled_rows,
       count(DISTINCT fixture) AS labelled_fixtures,
       40 AS min_train_events,
       GREATEST(0, 40 - count(DISTINCT fixture)) AS shortfall,
       count(DISTINCT fixture) FILTER (WHERE outcome = 1) AS fixtures_with_a_paying_row,
       sum(outcome) AS rows_paid, count(*) - sum(outcome) AS rows_not_paid
  FROM ok GROUP BY 1 ORDER BY 1;

\echo '== P4 · observable but NOT labelled, by reason (observer rules as in P2/P3) =='
WITH r AS (
  SELECT record_purpose, outcome_known, outcome, outcome_basis, outcome_at,
         CASE WHEN NULLIF(condition_id, '') IS NOT NULL THEN 'condition:' || condition_id
              WHEN NULLIF(event_key, '')    IS NOT NULL THEN 'event:' || event_key
              WHEN NULLIF(us_market_slug, '') IS NOT NULL THEN 'slug:' || us_market_slug END AS fixture,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN COALESCE(
                calibration_only_evidence #>> '{compared_at_the_displayed_price,price}',
                calibration_only_evidence #>> '{displayed_quote,acquisition_price}')
           WHEN 'ENTRY_DECISION' THEN executable_price::text END AS price_txt,
         CASE record_purpose
           WHEN 'CALIBRATION_ONLY' THEN calibration_only_evidence #>> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION' THEN risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_txt,
         probability, decided_at, observed_at
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), ok AS (
  SELECT * FROM r
   WHERE fixture IS NOT NULL
     AND price_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
     AND price_txt::double precision > 0 AND price_txt::double precision < 1
     AND probability IS NOT NULL AND decided_at IS NOT NULL AND observed_at IS NOT NULL
     AND receipt_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
)
SELECT record_purpose,
       CASE WHEN NOT outcome_known THEN 'NOT_SETTLED_OR_NOT_JOINED'
            WHEN outcome IS NULL OR outcome NOT IN (0, 1) THEN 'VOID_OR_NO_BINARY_OUTCOME'
            WHEN outcome_basis IS NULL THEN 'NO_OUTCOME_BASIS'
            WHEN outcome_basis NOT IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
                 THEN 'UNVERIFIED_BASIS:' || outcome_basis
            WHEN outcome_at IS NULL THEN 'NO_OUTCOME_TIME'
            ELSE 'LABELLED' END AS label_state,
       count(*) AS valuations, count(DISTINCT fixture) AS fixtures
  FROM ok GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== P5 · backfill walk: 2000 per step, newest id first; labelled fixtures reachable by step k =='
WITH r AS (
  SELECT id, record_purpose,
         CASE WHEN NULLIF(condition_id, '') IS NOT NULL THEN 'condition:' || condition_id
              WHEN NULLIF(event_key, '')    IS NOT NULL THEN 'event:' || event_key
              WHEN NULLIF(us_market_slug, '') IS NOT NULL THEN 'slug:' || us_market_slug END AS fixture,
         (outcome_known AND outcome IN (0, 1)
          AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
          AND outcome_at IS NOT NULL AND probability IS NOT NULL) AS labelled,
         (row_number() OVER (ORDER BY id DESC) - 1) / 2000 + 1 AS step
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), s AS (
  SELECT step, record_purpose, fixture, labelled FROM r
)
SELECT k.step AS through_step,
       (SELECT count(*) FROM r WHERE r.step <= k.step) AS rows_walked,
       (SELECT count(DISTINCT fixture) FROM s WHERE s.step <= k.step AND labelled
           AND record_purpose = 'CALIBRATION_ONLY') AS displayed_labelled_fixtures_upper_bound,
       (SELECT count(DISTINCT fixture) FROM s WHERE s.step <= k.step AND labelled
           AND record_purpose = 'ENTRY_DECISION') AS executable_labelled_fixtures_upper_bound
  FROM (SELECT DISTINCT step FROM r) k
 ORDER BY k.step
 LIMIT 40;

\echo '== P6 · labelled stored valuations by settlement day (before observer refusals; upper bound) =='
SELECT date_trunc('day', outcome_at)::date AS settled_day,
       count(DISTINCT CASE WHEN record_purpose = 'CALIBRATION_ONLY' THEN
             COALESCE('condition:' || NULLIF(condition_id, ''), 'event:' || NULLIF(event_key, ''),
                      'slug:' || us_market_slug) END) AS displayed_fixtures,
       count(DISTINCT CASE WHEN record_purpose = 'ENTRY_DECISION' THEN
             COALESCE('condition:' || NULLIF(condition_id, ''), 'event:' || NULLIF(event_key, ''),
                      'slug:' || us_market_slug) END) AS executable_fixtures
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
   AND outcome_known AND outcome IN (0, 1)
   AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
   AND outcome_at IS NOT NULL AND probability IS NOT NULL
 GROUP BY 1 ORDER BY 1 DESC
 LIMIT 30;
