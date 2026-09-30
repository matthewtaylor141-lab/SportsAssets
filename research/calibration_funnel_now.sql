-- READ-ONLY. THE CALIBRATION SHORTFALL, RECOMPUTED FROM THE CURRENT FUNNEL, AND
-- WHETHER ANY LEGITIMATELY TIMESTAMPED RECORD IS MISSING FROM IT.
-- K1-K3 are the reconcile file's funnel; C4 splits unresolved in-scope
-- fixtures by whether their start has passed (resolvable now if the outcome
-- join ran) vs still in the future; C5 counts in-scope fixtures whose outcome
-- is KNOWN but on an unverified basis (a join defect, not missing evidence).

\echo '== K1 · calibration funnel (90 days) =='
WITH w AS (
  SELECT * FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'),
s AS (
  SELECT * FROM w
   WHERE version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'),
f AS (
  SELECT DISTINCT ON (event_key) *
    FROM s WHERE coalesce(event_key, '') <> ''
   ORDER BY event_key, observed_at, id),
c AS (
  SELECT f.*,
         CASE WHEN outcome_basis = 'CONFIRMED_VOID' THEN 'VOID'
              WHEN NOT coalesce(outcome_known, false) THEN 'UNRESOLVED'
              WHEN outcome NOT IN (0, 1) OR outcome IS NULL THEN 'UNVERIFIED'
              WHEN outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                     'VENUE_REPORTED_OUTCOME')
                   AND probability IS NOT NULL THEN 'RESOLVED'
              WHEN outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                     'VENUE_REPORTED_OUTCOME')
                   THEN 'RESOLVED_BUT_NO_PROBABILITY'
              ELSE 'UNVERIFIED' END AS cls
    FROM f)
SELECT cls, count(*) AS fixtures,
       count(*) FILTER (WHERE decided_at < now() - interval '1 day') AS decided_over_1d_ago,
       min(decided_at) AS first, max(decided_at) AS last
  FROM c GROUP BY 1 ORDER BY 2 DESC;

\echo '== C4 · daily resolved-fixture accrual (in scope, last 21 days) =='
WITH f AS (
  SELECT DISTINCT ON (event_key) event_key, decided_at, outcome_known,
         outcome_basis, probability
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''
   ORDER BY event_key, observed_at, id)
SELECT date_trunc('day', decided_at)::date AS day, count(*) AS new_fixtures,
       count(*) FILTER (WHERE outcome_known AND outcome_basis IN
         ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME')
         AND probability IS NOT NULL) AS resolved_now
  FROM f WHERE decided_at >= now() - interval '21 days'
 GROUP BY 1 ORDER BY 1;

\echo '== C5 · outcome bases of in-scope fixtures (known and unknown) =='
SELECT coalesce(outcome_basis, 'NULL') AS basis,
       coalesce(outcome_known::text, 'NULL') AS known,
       count(DISTINCT event_key) AS fixtures
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '90 days'
   AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
   AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== C6 · Derek-model training source: resolved ENTRY_DECISION rows with price (all scopes) =='
SELECT count(*) AS rows_resolved_with_price,
       count(DISTINCT event_key) AS fixtures,
       min(decided_at) AS first, max(decided_at) AS last
  FROM external_valuations
 WHERE record_purpose = 'ENTRY_DECISION' AND outcome_known
   AND outcome IN (0, 1) AND executable_price IS NOT NULL;
