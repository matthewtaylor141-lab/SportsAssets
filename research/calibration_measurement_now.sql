-- READ-ONLY. THE WORKER'S OWN CALIBRATION MEASUREMENT, AS WRITTEN ON THE
-- CYCLE HEARTBEAT, AND RESOLVED IN-SCOPE FIXTURES BY THE DAY THEIR OUTCOME
-- BECAME KNOWN (first-recorded statement per fixture, verified basis).

\echo '== M1 · source_calibration_measurement (heartbeat) =='
SELECT jsonb_pretty(value->'source_calibration_measurement') AS measurement
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== M2 · independently resolved in-scope fixtures by outcome day =='
WITH f AS (
  SELECT DISTINCT ON (event_key) event_key, outcome_at, outcome_known,
         outcome_basis, probability
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''
   ORDER BY event_key, observed_at, id)
SELECT date_trunc('day', outcome_at)::date AS outcome_day, count(*) AS resolved
  FROM f
 WHERE outcome_known AND outcome_basis IN
       ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
   AND probability IS NOT NULL
 GROUP BY 1 ORDER BY 1;
