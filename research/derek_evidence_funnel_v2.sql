-- READ-ONLY. DEREK'S EVIDENCE FUNNEL, SCOPED CORRECTLY (v2).
--
-- v1 applied the BACKFILL observer rules to every valuation. They apply only
-- to rows written before the observer existed: observation_from_row
-- (derek_research.py, ddd4050) requires the original decision, price-receipt
-- and Pinnacle timestamps ONLY when mode != LIVE. A live observation does not
-- need a stored receipt, and the DISPLAYED_PRICE_AGE_UNKNOWN cohort has no
-- executable-freshness requirement at all (its price is the displayed quote,
-- its age recorded as unknown).
--
-- The authoritative training count is the fit's own: derek_research_observations
-- joined to a verified settlement label (derek_research.LABEL_SQL), distinct
-- fixtures per cohort, against MIN_TRAIN_EVENTS = 40.
--
-- F1 counts and latest timestamps: ENTRY_DECISION valuations,
--    CALIBRATION_ONLY valuations, research observations (by cohort and mode)
-- F2 authoritative eligible count per cohort (observations x verified labels)
-- F3 valuations that did NOT become observations, by cause class:
--    MISSING_ORIGINAL_RECEIPT (backfill only), NO_PINNACLE, NO_PRICE, other
-- F4 observations that are NOT labelled, by label cause:
--    unsettled / not joined, non-binary settlement, unverified basis, no time
-- F5 labelled observations by settlement day (accrual), per cohort
-- F6 live displayed-cohort observations: price timing basis as recorded
--    (proves the cohort runs without executable freshness and keeps unknown
--    timing as unknown)

\echo '== F1a · valuations of EXT_PINNACLE_DEVIG_V1_SHADOW by purpose =='
SELECT record_purpose, count(*) AS valuations,
       min(decided_at) AS first_decided, max(decided_at) AS latest_decided,
       count(*) FILTER (WHERE decided_at > now() - interval '24 hours') AS last_24h
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
 GROUP BY 1 ORDER BY 1;

\echo '== F1b · research observations by cohort, purpose and collection mode =='
SELECT cohort, record_purpose, collection_mode, evidence_class,
       count(*) AS observations, count(DISTINCT fixture) AS fixtures,
       min(decided_at) AS first_decided, max(decided_at) AS latest_decided,
       max(recorded_at) AS latest_recorded
  FROM derek_research_observations
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 3;

\echo '== F2 · AUTHORITATIVE eligible training evidence (observations x verified labels) =='
SELECT o.cohort,
       count(*) AS observations,
       count(*) FILTER (WHERE v.outcome_known AND v.outcome IN (0, 1)
                        AND v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
                        AND v.outcome_at IS NOT NULL) AS labelled_observations,
       count(DISTINCT o.fixture) FILTER (WHERE v.outcome_known AND v.outcome IN (0, 1)
                        AND v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
                        AND v.outcome_at IS NOT NULL) AS eligible_fixtures,
       40 AS min_train_events
  FROM derek_research_observations o
  JOIN external_valuations v ON v.id = o.valuation_id AND v.record_purpose = o.record_purpose
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
 GROUP BY 1 ORDER BY 1;

\echo '== F3 · valuations that did not become observations, by cause class =='
WITH v AS (
  SELECT v.id, v.record_purpose, v.decided_at, v.probability, v.observed_at,
         COALESCE('condition:' || NULLIF(v.condition_id, ''), 'event:' || NULLIF(v.event_key, ''),
                  'slug:' || NULLIF(v.us_market_slug, '')) AS fixture,
         CASE v.record_purpose
           WHEN 'CALIBRATION_ONLY' THEN COALESCE(
                v.calibration_only_evidence #>> '{compared_at_the_displayed_price,price}',
                v.calibration_only_evidence #>> '{displayed_quote,acquisition_price}')
           WHEN 'ENTRY_DECISION' THEN v.executable_price::text END AS price_txt,
         CASE v.record_purpose
           WHEN 'CALIBRATION_ONLY' THEN v.calibration_only_evidence #>> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION' THEN v.risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_txt,
         (o.valuation_id IS NOT NULL) AS observed
    FROM external_valuations v
    LEFT JOIN derek_research_observations o ON o.valuation_id = v.id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
)
SELECT record_purpose,
       CASE WHEN observed THEN 'BECAME_AN_OBSERVATION'
            WHEN fixture IS NULL THEN 'NO_FIXTURE'
            WHEN price_txt IS NULL THEN 'NO_PRICE'
            WHEN probability IS NULL THEN 'NO_PINNACLE_PROBABILITY'
            WHEN receipt_txt IS NULL THEN 'MISSING_ORIGINAL_RECEIPT_TIMESTAMP (backfill rule)'
            WHEN observed_at IS NULL THEN 'MISSING_ORIGINAL_PINNACLE_STAMP (backfill rule)'
            ELSE 'NOT_OBSERVED_OTHER' END AS cause,
       count(*) AS valuations, count(DISTINCT fixture) AS fixtures,
       max(decided_at) AS latest_decided
  FROM v GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== F4 · observations not labelled, by label cause =='
SELECT o.cohort,
       CASE WHEN NOT v.outcome_known THEN 'UNSETTLED_OR_NOT_JOINED'
            WHEN v.outcome IS NULL OR v.outcome NOT IN (0, 1) THEN 'NON_BINARY_SETTLEMENT'
            WHEN v.outcome_basis IS NULL OR v.outcome_basis NOT IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
                 THEN 'UNVERIFIED_BASIS:' || COALESCE(v.outcome_basis, 'NULL')
            WHEN v.outcome_at IS NULL THEN 'NO_OUTCOME_TIME'
            ELSE 'LABELLED' END AS label_state,
       count(*) AS observations, count(DISTINCT o.fixture) AS fixtures,
       max(o.decided_at) AS latest_decided
  FROM derek_research_observations o
  JOIN external_valuations v ON v.id = o.valuation_id
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== F5 · labelled observations by settlement day, per cohort =='
SELECT date_trunc('day', v.outcome_at)::date AS settled_day, o.cohort,
       count(*) AS labelled_observations, count(DISTINCT o.fixture) AS fixtures
  FROM derek_research_observations o
  JOIN external_valuations v ON v.id = o.valuation_id
 WHERE v.outcome_known AND v.outcome IN (0, 1)
   AND v.outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
   AND v.outcome_at IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1 DESC, 2;

\echo '== F6 · displayed-cohort timing as recorded (no executable freshness required) =='
SELECT cohort, price_basis, price_timing_basis,
       (price_received_at IS NULL) AS receipt_unknown,
       (price_source_ts IS NULL) AS venue_ts_unknown,
       price_source_ts_basis,
       count(*) AS observations, max(decided_at) AS latest_decided
  FROM derek_research_observations
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 7 DESC;
