-- PROVENANCE D1 PRE-DEPLOY CENSUS, part 1: model inventory and source sizing.
-- SELECT only. RC6.2 refuses a record-bound model when a stored training
-- vector no longer hashes (feature_sha, computed in Python) to the identity
-- stored with it. This part sizes the export: which models name which
-- source, how many ids, and how many stored (vector, identity) pairs each
-- source table holds. No vector values and no provenance records are printed.

\echo == 1. registry: models by key, state, provenance kind and source ==
SELECT model_key, state,
       coalesce(training_provenance->>'kind', '(none)') AS prov_kind,
       coalesce(training_provenance->>'source', '(absent: FUNDED_DECISIONS)') AS prov_source,
       count(*) AS models,
       sum(CASE WHEN jsonb_typeof(training_provenance->'decision_ids') = 'array'
                THEN jsonb_array_length(training_provenance->'decision_ids') END) AS ids_named
  FROM bettor_funded_models
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 3, 4;

\echo == 2. every APPROVED model, and the newest 12 CANDIDATE models per key ==
SELECT model_id, model_key, model_version, state,
       coalesce(training_provenance->>'kind', '(none)') AS prov_kind,
       coalesce(training_provenance->>'source', '(absent: FUNDED_DECISIONS)') AS prov_source,
       CASE WHEN jsonb_typeof(training_provenance->'decision_ids') = 'array'
            THEN jsonb_array_length(training_provenance->'decision_ids') END AS ids_named,
       train_rows, created_at, approved_at, fit_through
  FROM (SELECT m.*, row_number() OVER (PARTITION BY model_key, state
                                       ORDER BY created_at DESC) AS rn
          FROM bettor_funded_models m
         WHERE state IN ('APPROVED', 'CANDIDATE')) x
 WHERE state = 'APPROVED' OR rn <= 12
 ORDER BY model_key, state, created_at DESC;

\echo == 3. source tables: rows, stored vectors, stored identities, export size ==
SELECT 'bettor_funded_decisions' AS source_table,
       count(*) AS rows_total,
       count(features) AS with_vector,
       count(*) FILTER (WHERE features IS NOT NULL AND coalesce(feature_sha, '') <> '') AS vector_and_identity,
       round(avg(length(features::text))) AS avg_vector_chars,
       max(length(features::text)) AS max_vector_chars,
       max(length(decision_id)) AS max_id_chars
  FROM bettor_funded_decisions
UNION ALL
SELECT 'bettor_pair_observations', count(*), count(features),
       count(*) FILTER (WHERE features IS NOT NULL AND coalesce(feature_sha, '') <> ''),
       round(avg(length(features::text))), max(length(features::text)),
       max(length(observation_id))
  FROM bettor_pair_observations
UNION ALL
SELECT 'derek_entry_decisions', count(*), count(features),
       count(*) FILTER (WHERE features IS NOT NULL AND coalesce(feature_sha, '') <> ''),
       round(avg(length(features::text))), max(length(features::text)),
       max(length(decision_id))
  FROM derek_entry_decisions
UNION ALL
SELECT 'derek_research_observations', count(*), count(features),
       count(*) FILTER (WHERE features IS NOT NULL AND coalesce(feature_sha, '') <> ''),
       round(avg(length(features::text))), max(length(features::text)),
       max(length(observation_id))
  FROM derek_research_observations;

\echo == 4. pair observations by label and admission status (verify_provenance reads LABELLED + ADMITTED) ==
SELECT label_status, admission_status, count(*) AS rows
  FROM bettor_pair_observations
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo == 5. distinct ids named by APPROVED / CANDIDATE models, per source, and how many exist in the source table ==
WITH named AS (
  SELECT DISTINCT
         coalesce(m.training_provenance->>'source', 'FUNDED_DECISIONS') AS src,
         m.state, e.id
    FROM bettor_funded_models m
    CROSS JOIN LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(m.training_provenance->'decision_ids') = 'array'
              THEN m.training_provenance->'decision_ids' ELSE '[]'::jsonb END) AS e(id)
   WHERE m.state IN ('APPROVED', 'CANDIDATE')
)
SELECT n.src, n.state, count(*) AS distinct_ids,
       count(*) FILTER (WHERE coalesce(fd.decision_id, po.observation_id,
                                       de.decision_id, dr.observation_id) IS NOT NULL) AS found_in_source
  FROM named n
  LEFT JOIN bettor_funded_decisions fd
         ON n.src = 'FUNDED_DECISIONS' AND fd.decision_id = n.id
  LEFT JOIN bettor_pair_observations po
         ON n.src = 'PAIR_OBSERVATIONS' AND po.observation_id = n.id
  LEFT JOIN derek_entry_decisions de
         ON n.src = 'DEREK_ENTRY_DECISIONS' AND de.decision_id = n.id
  LEFT JOIN derek_research_observations dr
         ON n.src = 'DEREK_RESEARCH_OBSERVATIONS' AND dr.observation_id = n.id
 GROUP BY 1, 2
 ORDER BY 1, 2;
