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

\echo '== M1 · cycle cadence from the attempt ledger: one pass per cycle (24 h) =='
WITH p AS (
  SELECT pass_id, min(attempted_at) AS started
    FROM bettor_pair_observation_attempts
   WHERE attempted_at > now() - interval '24 hours'
   GROUP BY pass_id),
d AS (
  SELECT started, extract(epoch FROM started - lag(started) OVER (ORDER BY started)) AS gap_s
    FROM p)
SELECT count(*) AS passes, round(min(gap_s)) AS min_gap_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric) AS median_gap_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY gap_s))::numeric) AS p90_gap_s,
       round(max(gap_s)) AS max_gap_s
  FROM d WHERE gap_s IS NOT NULL;

\echo '== M2 · cycle cadence from entry-lane valuations: distinct decision instants per cycle (24 h) =='
WITH v AS (
  SELECT date_trunc('minute', decided_at) AS m FROM external_valuations
   WHERE decided_at > now() - interval '24 hours' GROUP BY 1),
c AS (
  SELECT m, extract(epoch FROM m - lag(m) OVER (ORDER BY m)) AS gap_s FROM v)
SELECT count(*) AS minutes_with_valuations,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric) AS median_gap_s,
       round(max(gap_s)) AS max_gap_s,
       count(*) FILTER (WHERE gap_s > 600) AS gaps_over_10_min
  FROM c WHERE gap_s IS NOT NULL;

\echo '== M3 · the latest heartbeat: step timings and servicing digests, if recorded =='
SELECT to_timestamp((value->>'at')::float8) AS written_at,
       value->'writer'->>'build' AS build,
       value->>'state' AS state,
       (value->'pair_observation'->>'elapsed_s') AS observation_elapsed_s,
       value->'timings' AS timings,
       value->'funded_servicing' IS NOT NULL AS has_funded_servicing,
       left(coalesce((value->'funded_servicing')::text, ''), 300) AS funded_servicing,
       left(coalesce((value->'xavier')::text, (value->'xavier_review')::text, ''), 300) AS xavier
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
