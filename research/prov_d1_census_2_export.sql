-- PROVENANCE D1 PRE-DEPLOY CENSUS, part 2: every stored (vector, identity)
-- pair in every source verify_provenance can read, exported for a LOCAL
-- recompute with bettor_funded_model.feature_sha. SELECT only.
--
-- The identity is a sha256 over Python's json.dumps of the vector as
-- json.loads parses features::text (no jsonb codec is registered), so it
-- cannot be recomputed in SQL; the vector is printed verbatim as
-- features::text, which is exactly the string the readers parse.
--
-- One line per row:  TAG|id|stored_feature_sha|R|models|features::text
--   TAG     FD bettor_funded_decisions, PO bettor_pair_observations,
--           DE derek_entry_decisions, DR derek_research_observations
--   R       1 when the row is in the set the source's labeller returns to
--           verify_provenance (its LABEL_SQL conditions), else 0
--   models  registry index (section 0) of every model whose
--           training_provenance.decision_ids names the row, comma separated
-- Rows ordered by primary key. Section 5 counts each source so the export
-- can be checked complete.

\echo == 0. registry legend: index, model, key, state, source, ids named ==
SELECT row_number() OVER (ORDER BY created_at, model_id) AS idx,
       model_id, model_key, state,
       coalesce(training_provenance->>'source', 'FUNDED_DECISIONS') AS src,
       CASE WHEN jsonb_typeof(training_provenance->'decision_ids') = 'array'
            THEN jsonb_array_length(training_provenance->'decision_ids') END AS ids_named
  FROM bettor_funded_models
 ORDER BY 1;

\echo == 1. FD bettor_funded_decisions ==
WITH reg AS (
  SELECT row_number() OVER (ORDER BY created_at, model_id) AS idx, m.*
    FROM bettor_funded_models m),
named AS (
  SELECT e.id, string_agg(reg.idx::text, ',' ORDER BY reg.idx) AS models
    FROM reg
    CROSS JOIN LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(reg.training_provenance->'decision_ids') = 'array'
              THEN reg.training_provenance->'decision_ids' ELSE '[]'::jsonb END) AS e(id)
   WHERE coalesce(reg.training_provenance->>'source', 'FUNDED_DECISIONS') = 'FUNDED_DECISIONS'
   GROUP BY e.id)
SELECT 'FD|' || d.decision_id || '|' || coalesce(d.feature_sha, '') || '|'
       || CASE WHEN d.group_id IS NOT NULL THEN '1' ELSE '0' END || '|'
       || coalesce(n.models, '') || '|' || d.features::text AS line
  FROM bettor_funded_decisions d
  LEFT JOIN named n ON n.id = d.decision_id
 WHERE d.features IS NOT NULL
 ORDER BY d.decision_id;

\echo == 2. PO bettor_pair_observations ==
WITH reg AS (
  SELECT row_number() OVER (ORDER BY created_at, model_id) AS idx, m.*
    FROM bettor_funded_models m),
named AS (
  SELECT e.id, string_agg(reg.idx::text, ',' ORDER BY reg.idx) AS models
    FROM reg
    CROSS JOIN LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(reg.training_provenance->'decision_ids') = 'array'
              THEN reg.training_provenance->'decision_ids' ELSE '[]'::jsonb END) AS e(id)
   WHERE reg.training_provenance->>'source' = 'PAIR_OBSERVATIONS'
   GROUP BY e.id)
SELECT 'PO|' || o.observation_id || '|' || coalesce(o.feature_sha, '') || '|'
       || CASE WHEN o.label_status = 'LABELLED'
                AND o.admission_status = 'ADMITTED_BY_DISCOVERY' THEN '1' ELSE '0' END || '|'
       || coalesce(n.models, '') || '|' || o.features::text AS line
  FROM bettor_pair_observations o
  LEFT JOIN named n ON n.id = o.observation_id
 WHERE o.features IS NOT NULL
 ORDER BY o.observation_id;

\echo == 3. DE derek_entry_decisions ==
WITH reg AS (
  SELECT row_number() OVER (ORDER BY created_at, model_id) AS idx, m.*
    FROM bettor_funded_models m),
named AS (
  SELECT e.id, string_agg(reg.idx::text, ',' ORDER BY reg.idx) AS models
    FROM reg
    CROSS JOIN LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(reg.training_provenance->'decision_ids') = 'array'
              THEN reg.training_provenance->'decision_ids' ELSE '[]'::jsonb END) AS e(id)
   WHERE reg.training_provenance->>'source' = 'DEREK_ENTRY_DECISIONS'
   GROUP BY e.id)
SELECT 'DE|' || d.decision_id || '|' || coalesce(d.feature_sha, '') || '|'
       || CASE WHEN EXISTS (
                SELECT 1 FROM external_valuations v
                 WHERE v.id = d.valuation_id
                   AND d.fixture IS NOT NULL
                   AND v.record_purpose = 'ENTRY_DECISION'
                   AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
                   AND v.outcome_known AND v.outcome IN (0, 1)
                   AND v.outcome_basis IS NOT NULL) THEN '1' ELSE '0' END || '|'
       || coalesce(n.models, '') || '|' || d.features::text AS line
  FROM derek_entry_decisions d
  LEFT JOIN named n ON n.id = d.decision_id
 WHERE d.features IS NOT NULL
 ORDER BY d.decision_id;

\echo == 4. DR derek_research_observations ==
WITH reg AS (
  SELECT row_number() OVER (ORDER BY created_at, model_id) AS idx, m.*
    FROM bettor_funded_models m),
named AS (
  SELECT e.id, string_agg(reg.idx::text, ',' ORDER BY reg.idx) AS models
    FROM reg
    CROSS JOIN LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(reg.training_provenance->'decision_ids') = 'array'
              THEN reg.training_provenance->'decision_ids' ELSE '[]'::jsonb END) AS e(id)
   WHERE reg.training_provenance->>'source' = 'DEREK_RESEARCH_OBSERVATIONS'
   GROUP BY e.id)
SELECT 'DR|' || o.observation_id || '|' || coalesce(o.feature_sha, '') || '|'
       || CASE WHEN EXISTS (
                SELECT 1 FROM external_valuations v
                 WHERE v.id = o.valuation_id
                   AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
                   AND v.record_purpose = o.record_purpose
                   AND v.outcome_known AND v.outcome IN (0, 1)
                   AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE',
                                                    'VENUE_REPORTED_OUTCOME'])
                   AND v.outcome_at IS NOT NULL) THEN '1' ELSE '0' END || '|'
       || coalesce(n.models, '') || '|' || o.features::text AS line
  FROM derek_research_observations o
  LEFT JOIN named n ON n.id = o.observation_id
 WHERE o.features IS NOT NULL
 ORDER BY o.observation_id;

\echo == 5. completeness counts (rows with a stored vector, per source) ==
SELECT 'FD' AS tag, count(*) AS rows_with_vector FROM bettor_funded_decisions WHERE features IS NOT NULL
UNION ALL SELECT 'PO', count(*) FROM bettor_pair_observations WHERE features IS NOT NULL
UNION ALL SELECT 'DE', count(*) FROM derek_entry_decisions WHERE features IS NOT NULL
UNION ALL SELECT 'DR', count(*) FROM derek_research_observations WHERE features IS NOT NULL;
