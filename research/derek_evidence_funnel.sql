-- READ-ONLY. DEREK'S RESEARCH-EVIDENCE FUNNEL: WHY ONLY A FEW ELIGIBLE FIXTURES.
--
-- Derek may fit a research model only when each cohort has at least
-- MIN_TRAIN_EVENTS = 40 DISTINCT labelled fixtures (bettor_funded_model). The
-- eligibility rules below are the observer's own (derek_research
-- observation_from_row in BACKFILL mode, and LABEL_SQL), reproduced in SQL and
-- cross-checked in research/derek_bootstrap_preview.sql. Fixture identity is
-- condition:, else event:, else slug: (derek_policy.fixture_of).
--
-- E0 stored valuations of EXT_PINNACLE_DEVIG_V1_SHADOW by purpose and day
-- E1 valuations per hour, last 72 h (collection uptime and outage gaps)
-- E2 observer outcome per stored valuation (OBSERVABLE or a refusal name)
-- E3 label state of OBSERVABLE valuations
-- E4 OBSERVABLE but unlabelled although the slug's event date is 2+ days old
--    (a label-join or settlement-read gap, not a market fact)
-- E5 ELIGIBLE: labelled observable fixtures per cohort vs 40
-- E6 eligible fixtures by settlement day (accrual rate)
-- E7 upstream funnel of the latest collection cycle per provider sport
-- E8 research observations recorded by the app, per cohort
-- E9 ENTRY_DECISION rows refused for BACKFILL_NO_PRICE_RECEIPT: which keys
--    their stored evidence actually holds, by day (is the receipt stored
--    under another path, or genuinely absent?)

\echo '== E0 · stored valuations by purpose and day =='
SELECT date_trunc('day', decided_at)::date AS day, record_purpose,
       count(*) AS valuations,
       count(DISTINCT COALESCE('condition:' || NULLIF(condition_id, ''),
                               'event:' || NULLIF(event_key, ''),
                               'slug:' || NULLIF(us_market_slug, ''))) AS fixtures
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2
 LIMIT 80;

\echo '== E1 · valuations per hour, last 72 h =='
SELECT date_trunc('hour', decided_at) AS hour, count(*) AS valuations,
       count(*) FILTER (WHERE record_purpose = 'CALIBRATION_ONLY') AS calibration_only,
       count(*) FILTER (WHERE record_purpose = 'ENTRY_DECISION') AS entry_decision
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at > now() - interval '72 hours'
 GROUP BY 1 ORDER BY 1;

\echo '== E2 · observer outcome per stored valuation =='
WITH v AS (
  SELECT id, record_purpose, decided_at, observed_at, probability,
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
), r AS (
  SELECT v.*,
         CASE WHEN fixture IS NULL THEN 'NO_FIXTURE'
              WHEN price_txt IS NULL OR price_txt !~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$' THEN 'NO_PRICE'
              WHEN NOT (price_txt::double precision > 0 AND price_txt::double precision < 1) THEN 'PRICE_OUT_OF_RANGE'
              WHEN probability IS NULL THEN 'NO_PINNACLE'
              WHEN decided_at IS NULL THEN 'BACKFILL_NO_DECISION_TIME'
              WHEN receipt_txt IS NULL OR receipt_txt !~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$' THEN 'BACKFILL_NO_PRICE_RECEIPT'
              WHEN observed_at IS NULL THEN 'BACKFILL_NO_PINNACLE_STAMP'
              ELSE 'OBSERVABLE' END AS observer
    FROM v
)
SELECT record_purpose, observer, count(*) AS valuations, count(DISTINCT fixture) AS fixtures,
       min(decided_at) AS oldest, max(decided_at) AS newest
  FROM r GROUP BY 1, 2 ORDER BY 1, (observer <> 'OBSERVABLE'), 3 DESC;

\echo '== E3 · label state of OBSERVABLE valuations =='
WITH ok AS (
  SELECT record_purpose, outcome_known, outcome, outcome_basis, outcome_at,
         COALESCE('condition:' || NULLIF(condition_id, ''), 'event:' || NULLIF(event_key, ''),
                  'slug:' || NULLIF(us_market_slug, '')) AS fixture
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
     AND probability IS NOT NULL AND decided_at IS NOT NULL AND observed_at IS NOT NULL
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

\echo '== E4 · unlabelled although the event date in the slug is 2+ days old =='
WITH u AS (
  SELECT record_purpose, us_market_slug, outcome_known, outcome_basis,
         COALESCE('condition:' || NULLIF(condition_id, ''), 'event:' || NULLIF(event_key, ''),
                  'slug:' || NULLIF(us_market_slug, '')) AS fixture,
         substring(us_market_slug from '([0-9]{4}-[0-9]{2}-[0-9]{2})') AS slug_day
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
     AND probability IS NOT NULL AND observed_at IS NOT NULL
     AND NOT (outcome_known AND outcome IN (0, 1)
              AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
              AND outcome_at IS NOT NULL)
)
SELECT record_purpose, slug_day, count(*) AS valuations, count(DISTINCT fixture) AS fixtures,
       count(*) FILTER (WHERE outcome_known) AS outcome_known_rows,
       string_agg(DISTINCT COALESCE(outcome_basis, '-'), ',') AS bases,
       (array_agg(DISTINCT us_market_slug))[1:3] AS examples
  FROM u
 WHERE slug_day IS NOT NULL AND slug_day::date < (now() AT TIME ZONE 'UTC')::date - 1
 GROUP BY 1, 2 ORDER BY 2 DESC, 1
 LIMIT 40;

\echo '== E5 · ELIGIBLE training evidence per cohort vs 40 =='
WITH r AS (
  SELECT record_purpose, outcome,
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
)
SELECT CASE record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                           ELSE 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
       count(*) AS labelled_rows, count(DISTINCT fixture) AS eligible_fixtures,
       GREATEST(0, 40 - count(DISTINCT fixture)) AS shortfall_to_40
  FROM r
 WHERE labelled AND fixture IS NOT NULL
   AND price_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
   AND price_txt::double precision > 0 AND price_txt::double precision < 1
   AND probability IS NOT NULL AND decided_at IS NOT NULL AND observed_at IS NOT NULL
   AND receipt_txt ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$'
 GROUP BY 1 ORDER BY 1;

\echo '== E6 · labelled observable fixtures by settlement day (accrual) =='
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
   AND outcome_at IS NOT NULL AND probability IS NOT NULL AND observed_at IS NOT NULL
 GROUP BY 1 ORDER BY 1 DESC LIMIT 30;

\echo '== E7 · upstream funnel of the latest collection cycle, per provider sport =='
SELECT to_timestamp((ingestion_state.value->>'written_at')::float8) AS cycle_written_at,
       s.key AS provider_sport,
       s.value->>'provider_events' AS provider_events,
       s.value->>'identity_resolved' AS identity_resolved,
       s.value->>'with_pinnacle_h2h' AS with_pinnacle_h2h,
       s.value->>'mapped_to_a_venue_contract' AS mapped,
       s.value->>'venue_markets_open_and_fresh' AS venue_open_fresh,
       s.value->>'evaluated' AS evaluated,
       s.value->>'written' AS written,
       s.value->>'recorded_for_calibration_only' AS calib_only,
       left((s.value->'refusals')::text, 300) AS refusals
  FROM ingestion_state, jsonb_each(ingestion_state.value->'funnel_by_provider_sport') s
 WHERE ingestion_state.key = 'ext_pinnacle_last_cycle'
 ORDER BY (s.value->>'provider_events')::int DESC NULLS LAST;

\echo '== E8 · research observations recorded by the app, per cohort =='
SELECT cohort, record_purpose, count(*) AS observations, count(DISTINCT fixture) AS fixtures,
       min(decided_at) AS oldest, max(decided_at) AS newest
  FROM derek_research_observations GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== E9a · ENTRY_DECISION evidence keys, receipt present vs absent, by day =='
SELECT date_trunc('day', decided_at)::date AS day,
       (risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}') IS NOT NULL AS has_receipt,
       count(*) AS valuations,
       string_agg(DISTINCT coalesce((SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(COALESCE(risk_verdict, '{}'::jsonb)) k), '-'), ' | ') AS risk_verdict_keys
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'ENTRY_DECISION'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== E9b · freshness_evidence and venue_clock keys on rows WITHOUT the receipt =='
SELECT (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(COALESCE(risk_verdict->'freshness_evidence', '{}'::jsonb)) k) AS freshness_keys,
       (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(COALESCE(risk_verdict #> '{freshness_evidence,venue_clock}', '{}'::jsonb)) k) AS venue_clock_keys,
       count(*) AS valuations, min(decided_at) AS oldest, max(decided_at) AS newest
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'ENTRY_DECISION'
   AND (risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}') IS NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo '== E9c · one sample of the evidence on a row WITHOUT the receipt (shortened) =='
SELECT id, decided_at, left(risk_verdict::text, 1500) AS risk_verdict
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'ENTRY_DECISION'
   AND (risk_verdict #>> '{freshness_evidence,venue_clock,our_response_received_at}') IS NULL
   AND probability IS NOT NULL
 ORDER BY decided_at DESC LIMIT 1;
