-- READ-ONLY. THE CANCELLATION CLAUSES THE COLLECTOR ACTUALLY READ, AND WHAT
-- IT RECORDED WITH AND WITHOUT ADMISSION (build 2026-10-01 onward).
--
--   C1  per venue document (prose sha): the venue's own sentences naming a
--       cancellation, verbatim as captured, and the treatment read from them
--   C2  siblings that reached discovery: cancellation treatment x the regions
--       left undetermined x whether the pair was observable without admission
--   C3  observations by admission status, ordinary-play taxonomy, pair
--       cancellation verdict and label status, in fixtures and rows
--   C4  the read budget: eligible siblings examined, deferred, exhausted, and
--       what was excluded before any read
--   C5  attempts by conclusion
--
-- No account, balance or credential field is selected.

\echo '== C1 · cancellation wording read, per venue document =='
SELECT a.detail->'held'->'cancellation'->>'prose_sha256' AS prose_sha,
       a.detail->'held'->'cancellation'->>'interpretation' AS interpretation,
       a.detail->'held'->'cancellation'->>'resolution' AS resolution,
       a.detail->'held'->'cancellation'->>'refusal' AS refusal,
       a.detail->'held'->'cancellation'->>'clause' AS clause,
       a.detail->'held'->'cancellation'->>'venue_sentences_naming_cancellation'
         AS venue_sentences,
       (a.detail->'held'->'cancellation'->>'prose_chars')::int AS prose_chars,
       count(*) AS attempts, count(DISTINCT a.fixture) AS fixtures,
       min(a.us_market_slug) AS example
  FROM bettor_pair_observation_attempts a
 WHERE a.detail->'held' ? 'cancellation'
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY attempts DESC LIMIT 30;

\echo '== C2 · siblings at discovery: cancellation treatment x undetermined regions =='
WITH sib AS (
  SELECT a.fixture, e
    FROM bettor_pair_observation_attempts a,
         jsonb_array_elements(CASE WHEN jsonb_typeof(a.detail->'siblings') = 'array'
                                   THEN a.detail->'siblings' ELSE '[]'::jsonb END) e
   WHERE a.detail->'held' ? 'cancellation' AND e->>'stage' = 'DISCOVERY')
SELECT e->'cancellation'->>'interpretation' AS sibling_treatment,
       e->>'refusal' AS refusal,
       coalesce((e->'undetermined_regions')::text, '(none)') AS undetermined,
       coalesce(e->>'observable_without_admission', 'false') AS observable,
       regexp_replace(coalesce(e->>'sports_type', ''), '^[a-z]+_', '') AS contract_type,
       count(*) AS siblings, count(DISTINCT fixture) AS fixtures,
       min(e->>'cancellation_clause') AS example_clause
  FROM sib GROUP BY 1, 2, 3, 4, 5 ORDER BY siblings DESC LIMIT 30;

\echo '== C3 · observations by admission status =='
SELECT admission_status,
       structure->'ordinary_play'->>'taxonomy_if_played' AS taxonomy_if_played,
       cancellation_terms->'pair'->>'verdict' AS pair_cancellation,
       label_status, coalesce(label_why, '') AS label_why,
       count(*) AS observations, count(DISTINCT fixture) AS fixtures,
       min(observed_at) AS first, max(observed_at) AS last
  FROM bettor_pair_observations
 GROUP BY 1, 2, 3, 4, 5 ORDER BY observations DESC;

\echo '== C3b · the newest observations, one line each =='
SELECT observation_id, observed_at, fixture, primary_slug, primary_side,
       hedge_slug, hedge_side, primary_cost_cents, hedge_cost_cents,
       admission_status, unresolved::text AS unresolved,
       cancellation_terms->'held'->>'interpretation' AS held_cancel,
       cancellation_terms->'hedge'->>'interpretation' AS hedge_cancel,
       label_status
  FROM bettor_pair_observations ORDER BY observed_at DESC LIMIT 25;

\echo '== C4 · read budget per attempt (newest 40) =='
SELECT attempt_id, attempted_at, fixture, us_market_slug,
       (detail->>'siblings_eligible')::int AS eligible,
       (detail->>'siblings_examined')::int AS examined,
       (detail->>'siblings_deferred')::int AS deferred,
       (detail->>'siblings_exhausted')::int AS exhausted,
       detail->'excluded_before_reads' AS excluded_before_reads,
       (detail->>'observable_without_admission')::int AS observable,
       detail->>'conclusion' AS conclusion
  FROM bettor_pair_observation_attempts
 WHERE detail ? 'siblings_eligible'
 ORDER BY attempt_id DESC LIMIT 40;

\echo '== C5 · attempts by conclusion (this build) =='
SELECT detail->>'conclusion' AS conclusion, outcome, count(*) AS attempts,
       count(DISTINCT fixture) AS fixtures, sum(observations_written) AS written,
       min(attempted_at) AS first, max(attempted_at) AS last
  FROM bettor_pair_observation_attempts
 WHERE detail ? 'siblings_eligible' OR detail->'held' ? 'cancellation'
 GROUP BY 1, 2 ORDER BY attempts DESC;

