-- READ-ONLY. DEREK RESEARCH BOOTSTRAP, READ BACK FROM PRODUCTION AFTER THE
-- RELEASE THAT ADDS derek_research_observations (migrations 170, 171, 172,
-- 180, 181 applied).
--
-- STATUS 2026-10-01: drafted against the REJECTED fix 37814ad (its index
-- derek_research_model_runs_one_decisive_per_day). The replacement fix is
-- being rebuilt from ddd4050; re-check B0/B5 against the final candidate's
-- migration 181 before running this in production. to_regclass() on an
-- absent index returns NULL rather than failing.
--
-- What the app has recorded, not a preview (the preview, computed from
-- external_valuations alone before the release, is
-- research/derek_bootstrap_preview.sql). Every rule below is the code's own,
-- restated in SQL; nothing here writes, and the whole file runs in one
-- REPEATABLE READ, READ ONLY transaction so every section sees one snapshot.
--
-- B0 schema this was read against (migration ledger, 181's index)
-- B1 backfill state (ingestion_state 'derek_research_backfill') and the
--    stored valuations of the entry experiment that have NO research
--    observation, by record_purpose: still ahead of the backfill cursor, in
--    the live window, or already walked past
-- B2 research observations by cohort and collection mode (live / backfill):
--    rows and DISTINCT fixtures
-- B3 LABELLED research fixtures by cohort, exactly as
--    derek_research.labelled_observations joins them, vs MIN_TRAIN_EVENTS;
--    then the labelled stored-valuation fixtures (the upper bound) and where
--    each stands: in the training set, still to come from the backfill, or
--    refused
-- B4 exclusions: unobserved stored valuations by the observer's named
--    reason; observations that are not (yet) labels, by reason
-- B5 derek_research_model_runs and derek_research_model_attempts (newest 10)
-- B6 CANDIDATE models fitted on the research source (bettor_funded_models)
--
-- CONSTANTS, HARDCODED FROM THE CODE THIS RELEASE SHIPS (rel-boot):
--   'EXT_PINNACLE_DEVIG_V1_SHADOW'   bettor_external_shadow.EXPERIMENT_ID
--   'VENUE_SETTLEMENT_PRICE',
--   'VENUE_REPORTED_OUTCOME'         derek_research.LABEL_BASES
--   1800 s                           derek_research.LOOKBACK_S (2 * 900): the
--                                    live window's lower edge is
--                                    now - max(1800, cycle elapsed + 60); 1800
--                                    is used here
--   2000                             derek_research.BACKFILL_PER_CYCLE
--   40                               bettor_funded_model.MIN_TRAIN_EVENTS
--   10                               bettor_funded_model.CANDIDATE_REFIT_MIN_NEW_EVENTS
--   'derek-research-auto'            derek_research.AUTO_MODEL_PREFIX
--   'derek_entry_payout_event'       bettor_funded_model.KEY_ENTRY_PAYOUT
--   'DEREK_RESEARCH_OBSERVATIONS'    bettor_funded_model.SOURCE_RESEARCH_OBSERVATIONS
--   cohorts DISPLAYED_PRICE_AGE_UNKNOWN (CALIBRATION_ONLY) and
--           EXECUTABLE_PRICE_CURRENT (ENTRY_DECISION): never pooled.
--
-- WHAT COUNTS. The fit counts DISTINCT FIXTURES per cohort
-- (derek_policy.fixture_of: condition:<id>, else event:<key>, else
-- slug:<slug>, an empty string counting as absent). Backfilled
-- (RETROSPECTIVE_STORED) rows TRAIN; they never count as prospective
-- evaluation evidence, and nothing here promotes or approves anything.
--
-- Nothing here selects a credential, a secret, an account or a message body.
-- No training_provenance document is selected whole (it carries every
-- training record); only named scalar fields are read from it.
--
-- Run:  psql <production DSN> -X -v ON_ERROR_STOP=1 -f research/derek_research_bootstrap_readback.sql
--
-- Cross-checked 2026-10-01 on a scratch DB migrated from mp2 by the rel-boot
-- worktree (37814ad, migrations through 181) with SYNTHETIC valuations
-- covering every observer refusal, both purposes, every label state, the
-- live window and one row above the backfill cursor. Against the real code
-- (DR.observe, DR.backfill stepped at limit 17, DR.daily_model_run twice in
-- one UTC day, paper_derek.research_model), 21 of 21 comparisons agreed:
-- B1b remaining observed / refused / steps / gap, B3a labelled rows and
-- fixtures per cohort (partial and final), B3b projection = final count, B2,
-- B4a reasons (live and backfill), B5b runs, B6b pick and n_events.

\set ON_ERROR_STOP on
\pset null 'NULL'

BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '120s';
SET LOCAL lock_timeout = '5s';
SET LOCAL TIME ZONE 'UTC';

\echo '== B0 · read instant and schema =='
SELECT now() AS read_at_utc, (now() AT TIME ZONE 'UTC')::date AS utc_day;
SELECT version, applied_at
  FROM schema_migrations
 WHERE version ~ '^(17[0-9]|18[0-9])_'
 ORDER BY version;
-- 181: an INSUFFICIENT run no longer closes the UTC day. Both columns must
-- read (true, false) for the same-day run to be possible
-- (derek_research._same_day_run_possible).
SELECT to_regclass('derek_research_model_runs_one_decisive_per_day') IS NOT NULL
           AS decisive_run_index_181_present,
       EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('derek_research_model_runs')
                  AND conname = 'derek_research_model_runs_one_per_day')
           AS one_row_per_day_constraint_170_present;

\echo '== B1a · backfill state (ingestion_state key derek_research_backfill; no row = not started) =='
SELECT k.key,
       s.key IS NOT NULL AS state_row_present,
       s.value -> 'cursor_below_id' AS cursor_below_id,
       s.value -> 'backfilled_total' AS backfilled_total,
       coalesce(s.value -> 'exhausted' = 'true'::jsonb, false) AS exhausted,
       CASE WHEN jsonb_typeof(s.value -> 'exhausted_at') = 'number'
            THEN to_timestamp((s.value ->> 'exhausted_at')::float8) END
           AS exhausted_at,
       CASE WHEN jsonb_typeof(s.value -> 'last_step_at') = 'number'
            THEN to_timestamp((s.value ->> 'last_step_at')::float8) END
           AS last_step_at,
       CASE WHEN jsonb_typeof(s.value -> 'last_step_at') = 'number'
            THEN round((extract(epoch FROM now())
                        - (s.value ->> 'last_step_at')::float8)::numeric, 1) END
           AS last_step_age_s
  FROM (VALUES ('derek_research_backfill')) AS k(key)
  LEFT JOIN ingestion_state s ON s.key = k.key;

\echo '== B1b · stored valuations with NO research observation, by record_purpose (remaining backfill) =='
-- walk position of an unobserved valuation:
--   IN_LIVE_WINDOW    decided within LOOKBACK_S: the live step's to take
--   PENDING_BACKFILL  older, below the cursor (or no cursor yet) and the
--                     backfill is not exhausted: a later step will visit it
--   WALKED_PAST       older and above the cursor, or the backfill is
--                     exhausted: refused by name, or (no reason derivable)
--                     a collection gap no step will revisit -- expect 0
-- remaining_* : what the remaining backfill steps will record (observer
-- reason none) or refuse. steps_to_exhaust counts the final empty step that
-- marks the backfill exhausted; the walk is ONE stream over both purposes,
-- so it is given on the ALL row only.
WITH st AS (
  SELECT max(CASE WHEN jsonb_typeof(value -> 'cursor_below_id') = 'number'
                  THEN (value ->> 'cursor_below_id')::numeric END) AS cursor_below_id,
         coalesce(bool_or(value -> 'exhausted' = 'true'::jsonb), false) AS exhausted
    FROM ingestion_state WHERE key = 'derek_research_backfill'
), lf AS (
  SELECT DISTINCT o.cohort, o.fixture
    FROM derek_research_observations o
    JOIN external_valuations v ON v.id = o.valuation_id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose = o.record_purpose
     AND v.outcome_known AND v.outcome IN (0, 1)
     AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
     AND v.outcome_at IS NOT NULL
     AND o.decided_at <= now() AND v.outcome_at <= now()
), raw AS (
  SELECT v.id, v.record_purpose, v.decided_at, v.observed_at, v.probability,
         v.executable_price,
         CASE v.record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                               WHEN 'ENTRY_DECISION'   THEN 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
         CASE WHEN coalesce(v.condition_id, '')   <> '' THEN 'condition:' || v.condition_id
              WHEN coalesce(v.event_key, '')      <> '' THEN 'event:' || v.event_key
              WHEN coalesce(v.us_market_slug, '') <> '' THEN 'slug:' || v.us_market_slug END AS fixture,
         CASE WHEN v.record_purpose = 'CALIBRATION_ONLY' THEN coalesce(
                NULLIF(v.calibration_only_evidence #> '{compared_at_the_displayed_price,price}', 'null'::jsonb),
                v.calibration_only_evidence #> '{displayed_quote,acquisition_price}') END AS price_j,
         CASE v.record_purpose
           WHEN 'CALIBRATION_ONLY' THEN v.calibration_only_evidence #> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION'   THEN v.risk_verdict #> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_j,
         (v.outcome_known AND v.outcome IN (0, 1)
          AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
          AND v.outcome_at IS NOT NULL) AS labelled,
         EXISTS (SELECT 1 FROM derek_research_observations o
                  WHERE o.valuation_id = v.id) AS observed
    FROM external_valuations v
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), parsed AS (
  SELECT r.*,
         CASE WHEN r.record_purpose = 'ENTRY_DECISION' THEN
                CASE WHEN r.executable_price IS NULL THEN 'NONE'
                     WHEN r.executable_price > 0 AND r.executable_price < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'number' THEN
                CASE WHEN (r.price_j #>> '{}')::numeric > 0
                      AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'boolean' THEN 'OUT_OF_RANGE'
              WHEN jsonb_typeof(r.price_j) = 'string' THEN
                CASE WHEN (r.price_j #>> '{}') ~* '^\s*[-+]?(nan|inf|infinity)\s*$' THEN 'OUT_OF_RANGE'
                     WHEN (r.price_j #>> '{}') ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$' THEN
                       CASE WHEN (r.price_j #>> '{}')::numeric > 0
                             AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                            ELSE 'OUT_OF_RANGE' END
                     ELSE 'NONE' END
              ELSE 'NONE' END AS price_class,
         CASE jsonb_typeof(r.receipt_j)
           WHEN 'number'  THEN true
           WHEN 'boolean' THEN true
           WHEN 'string'  THEN (r.receipt_j #>> '{}')
                ~* '^\s*[-+]?(nan|inf|infinity|([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?)\s*$'
           ELSE false END AS has_receipt,
         r.decided_at > now() - interval '1800 seconds' AS in_live_window
    FROM raw r
), cv AS (
  SELECT p.*,
         (lf.fixture IS NOT NULL) AS fixture_in_training,
         CASE WHEN p.observed THEN 'OBSERVED'
              WHEN p.in_live_window THEN 'IN_LIVE_WINDOW'
              WHEN NOT st.exhausted
                   AND (st.cursor_below_id IS NULL OR p.id < st.cursor_below_id)
                   THEN 'PENDING_BACKFILL'
              ELSE 'WALKED_PAST' END AS walk,
         -- derek_research.observation_from_row, in its order; the three
         -- BACKFILL_* reasons apply only outside the live window
         CASE WHEN p.fixture IS NULL              THEN 'NO_FIXTURE_IDENTITY_ON_THE_VALUATION'
              WHEN p.price_class = 'NONE'         THEN 'NO_PRICE_ON_THE_VALUATION'
              WHEN p.price_class = 'OUT_OF_RANGE' THEN 'PRICE_NOT_STRICTLY_BETWEEN_0_AND_1'
              WHEN p.probability IS NULL          THEN 'NO_PINNACLE_PROBABILITY_ON_THE_VALUATION'
              WHEN p.decided_at IS NULL           THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_DECISION_TIME'
              WHEN p.in_live_window               THEN NULL
              WHEN NOT p.has_receipt              THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PRICE_RECEIPT_TIME'
              WHEN p.observed_at IS NULL          THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PINNACLE_OBSERVED_AT'
         END AS reason
    FROM parsed p
    CROSS JOIN st
    LEFT JOIN lf ON lf.cohort = p.cohort AND lf.fixture = p.fixture
)
SELECT CASE WHEN GROUPING(record_purpose) = 1 THEN 'ALL (one walk)' ELSE record_purpose END
           AS record_purpose,
       count(*) AS stored_valuations,
       count(*) FILTER (WHERE observed) AS observed,
       count(*) FILTER (WHERE NOT observed) AS not_observed,
       count(*) FILTER (WHERE walk = 'IN_LIVE_WINDOW') AS in_live_window,
       count(*) FILTER (WHERE walk = 'PENDING_BACKFILL') AS remaining_backfill_rows,
       count(*) FILTER (WHERE walk = 'PENDING_BACKFILL' AND reason IS NULL)
           AS remaining_will_be_observed,
       count(*) FILTER (WHERE walk = 'PENDING_BACKFILL' AND reason IS NOT NULL)
           AS remaining_will_be_refused,
       count(DISTINCT fixture) FILTER (WHERE walk = 'PENDING_BACKFILL' AND reason IS NULL
                                         AND labelled AND NOT fixture_in_training)
           AS remaining_new_labelled_fixtures,
       CASE WHEN GROUPING(record_purpose) = 1 THEN
            CASE WHEN bool_or(st_exhausted) THEN 0
                 ELSE ceil(count(*) FILTER (WHERE walk = 'PENDING_BACKFILL') / 2000.0)::int + 1 END
       END AS steps_to_exhaust,
       count(*) FILTER (WHERE walk = 'WALKED_PAST' AND reason IS NOT NULL)
           AS walked_past_refused_by_name,
       count(*) FILTER (WHERE walk = 'WALKED_PAST' AND reason IS NULL)
           AS walked_past_unobserved_gap
  FROM (SELECT cv.*, st.exhausted AS st_exhausted FROM cv CROSS JOIN st) x
 GROUP BY GROUPING SETS ((record_purpose), ())
 ORDER BY GROUPING(record_purpose), record_purpose;

\echo '== B2 · research observations by cohort and collection mode (rows and DISTINCT fixtures) =='
SELECT CASE WHEN GROUPING(cohort) = 1 THEN 'ALL' ELSE cohort END AS cohort,
       CASE WHEN GROUPING(collection_mode) = 1 THEN 'ALL' ELSE collection_mode END
           AS collection_mode,
       CASE WHEN GROUPING(collection_mode) = 1 THEN NULL
            ELSE min(evidence_class) END AS evidence_class,
       count(*) AS rows, count(DISTINCT fixture) AS distinct_fixtures,
       count(*) FILTER (WHERE model_p IS NOT NULL) AS rows_with_frozen_model_p,
       min(decided_at) AS oldest_decided, max(decided_at) AS newest_decided,
       min(recorded_at) AS first_recorded, max(recorded_at) AS last_recorded
  FROM derek_research_observations
 GROUP BY GROUPING SETS ((cohort, collection_mode), (cohort), ())
 ORDER BY GROUPING(cohort), cohort, GROUPING(collection_mode), collection_mode;

\echo '== B3a · LABELLED research fixtures by cohort (labelled_observations join, cut off at now) vs MIN_TRAIN_EVENTS = 40 =='
-- The join is derek_research.LABEL_SQL: same experiment, same
-- record_purpose, outcome_known, outcome in (0, 1), outcome_basis in
-- LABEL_BASES, outcome_at not null; decided and resolved by the read instant
-- (the fit's through = outcomes_through = its run instant).
-- fit_due_now mirrors daily_model_run: the cohort has >= 40 labelled
-- fixtures, today has no decisive run yet, and either no scheduled attempt
-- exists or the last one is not today's and the cohort grew by >= 10
-- fixtures since it.
WITH lab AS (
  SELECT o.cohort, o.fixture, o.evidence_class, v.outcome, v.outcome_at
    FROM derek_research_observations o
    JOIN external_valuations v ON v.id = o.valuation_id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose = o.record_purpose
     AND v.outcome_known AND v.outcome IN (0, 1)
     AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
     AND v.outcome_at IS NOT NULL
     AND o.decided_at <= now() AND v.outcome_at <= now()
), today AS (
  SELECT EXISTS (SELECT 1 FROM derek_research_model_runs
                  WHERE run_day = (now() AT TIME ZONE 'UTC')::date
                    AND outcome <> 'INSUFFICIENT_LABELLED_FIXTURES') AS decisive_today
), per AS (
  SELECT c.cohort,
         count(l.fixture) AS labelled_rows,
         count(DISTINCT l.fixture) AS labelled_fixtures,
         count(DISTINCT l.fixture) FILTER (WHERE l.evidence_class = 'RETROSPECTIVE_STORED')
             AS fixtures_via_backfill,
         count(DISTINCT l.fixture) FILTER (WHERE l.evidence_class = 'PROSPECTIVE_LIVE')
             AS fixtures_via_live,
         count(*) FILTER (WHERE l.outcome = 1) AS rows_paid,
         count(*) FILTER (WHERE l.outcome = 0) AS rows_not_paid,
         max(l.outcome_at) AS newest_outcome_at
    FROM (VALUES ('DISPLAYED_PRICE_AGE_UNKNOWN'), ('EXECUTABLE_PRICE_CURRENT')) AS c(cohort)
    LEFT JOIN lab l ON l.cohort = c.cohort
   GROUP BY c.cohort
)
SELECT per.cohort, per.labelled_rows, per.labelled_fixtures,
       40 AS min_train_events,                       -- bettor_funded_model.MIN_TRAIN_EVENTS
       greatest(0, 40 - per.labelled_fixtures) AS shortfall_fixtures,
       per.labelled_fixtures >= 40 AS fit_eligible,
       per.fixtures_via_backfill, per.fixtures_via_live,
       per.rows_paid, per.rows_not_paid, per.newest_outcome_at,
       p.attempt_id AS last_scheduled_attempt,
       p.train_fixtures AS last_attempt_train_fixtures,
       per.labelled_fixtures - p.train_fixtures AS new_fixtures_since_last_attempt,
       today.decisive_today AS today_has_decisive_run,
       (per.labelled_fixtures >= 40 AND NOT today.decisive_today
        AND (p.attempt_id IS NULL
             OR (p.attempt_id <> 'derek-research-auto:' || per.cohort || ':'
                                  || (now() AT TIME ZONE 'UTC')::date
                 AND per.labelled_fixtures - p.train_fixtures >= 10)))
           AS fit_due_now
  FROM per
  CROSS JOIN today
  LEFT JOIN LATERAL (
        SELECT a.attempt_id, a.train_fixtures
          FROM derek_research_model_attempts a
         WHERE a.attempt_id LIKE 'derek-research-auto:' || per.cohort || ':%'
         ORDER BY a.attempted_at DESC LIMIT 1) p ON true
 ORDER BY per.cohort;

\echo '== B3b · labelled STORED-valuation fixtures (upper bound) and where each stands =='
-- A fixture with at least one labelled stored valuation, counted once per
-- cohort under its best standing: in_training (B3a) > still to come from
-- the backfill > in the live window > observable but walked past (gap) >
-- every labelled row refused by the observer (reasons in B4a).
-- projected_after_backfill = in_training + pending_backfill + live_window:
-- what B3a will read once the backfill is exhausted, if nothing else settles.
WITH st AS (
  SELECT max(CASE WHEN jsonb_typeof(value -> 'cursor_below_id') = 'number'
                  THEN (value ->> 'cursor_below_id')::numeric END) AS cursor_below_id,
         coalesce(bool_or(value -> 'exhausted' = 'true'::jsonb), false) AS exhausted
    FROM ingestion_state WHERE key = 'derek_research_backfill'
), lf AS (
  SELECT DISTINCT o.cohort, o.fixture
    FROM derek_research_observations o
    JOIN external_valuations v ON v.id = o.valuation_id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose = o.record_purpose
     AND v.outcome_known AND v.outcome IN (0, 1)
     AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
     AND v.outcome_at IS NOT NULL
     AND o.decided_at <= now() AND v.outcome_at <= now()
), raw AS (
  SELECT v.id, v.record_purpose, v.decided_at, v.observed_at, v.probability,
         v.executable_price,
         CASE v.record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                               WHEN 'ENTRY_DECISION'   THEN 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
         CASE WHEN coalesce(v.condition_id, '')   <> '' THEN 'condition:' || v.condition_id
              WHEN coalesce(v.event_key, '')      <> '' THEN 'event:' || v.event_key
              WHEN coalesce(v.us_market_slug, '') <> '' THEN 'slug:' || v.us_market_slug END AS fixture,
         CASE WHEN v.record_purpose = 'CALIBRATION_ONLY' THEN coalesce(
                NULLIF(v.calibration_only_evidence #> '{compared_at_the_displayed_price,price}', 'null'::jsonb),
                v.calibration_only_evidence #> '{displayed_quote,acquisition_price}') END AS price_j,
         CASE v.record_purpose
           WHEN 'CALIBRATION_ONLY' THEN v.calibration_only_evidence #> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION'   THEN v.risk_verdict #> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_j,
         (v.outcome_known AND v.outcome IN (0, 1)
          AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
          AND v.outcome_at IS NOT NULL) AS labelled,
         EXISTS (SELECT 1 FROM derek_research_observations o
                  WHERE o.valuation_id = v.id) AS observed
    FROM external_valuations v
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), parsed AS (
  SELECT r.*,
         CASE WHEN r.record_purpose = 'ENTRY_DECISION' THEN
                CASE WHEN r.executable_price IS NULL THEN 'NONE'
                     WHEN r.executable_price > 0 AND r.executable_price < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'number' THEN
                CASE WHEN (r.price_j #>> '{}')::numeric > 0
                      AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'boolean' THEN 'OUT_OF_RANGE'
              WHEN jsonb_typeof(r.price_j) = 'string' THEN
                CASE WHEN (r.price_j #>> '{}') ~* '^\s*[-+]?(nan|inf|infinity)\s*$' THEN 'OUT_OF_RANGE'
                     WHEN (r.price_j #>> '{}') ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$' THEN
                       CASE WHEN (r.price_j #>> '{}')::numeric > 0
                             AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                            ELSE 'OUT_OF_RANGE' END
                     ELSE 'NONE' END
              ELSE 'NONE' END AS price_class,
         CASE jsonb_typeof(r.receipt_j)
           WHEN 'number'  THEN true
           WHEN 'boolean' THEN true
           WHEN 'string'  THEN (r.receipt_j #>> '{}')
                ~* '^\s*[-+]?(nan|inf|infinity|([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?)\s*$'
           ELSE false END AS has_receipt,
         r.decided_at > now() - interval '1800 seconds' AS in_live_window
    FROM raw r
), cv AS (
  SELECT p.*,
         (lf.fixture IS NOT NULL) AS fixture_in_training,
         CASE WHEN p.observed THEN 'OBSERVED'
              WHEN p.in_live_window THEN 'IN_LIVE_WINDOW'
              WHEN NOT st.exhausted
                   AND (st.cursor_below_id IS NULL OR p.id < st.cursor_below_id)
                   THEN 'PENDING_BACKFILL'
              ELSE 'WALKED_PAST' END AS walk,
         CASE WHEN p.fixture IS NULL              THEN 'NO_FIXTURE_IDENTITY_ON_THE_VALUATION'
              WHEN p.price_class = 'NONE'         THEN 'NO_PRICE_ON_THE_VALUATION'
              WHEN p.price_class = 'OUT_OF_RANGE' THEN 'PRICE_NOT_STRICTLY_BETWEEN_0_AND_1'
              WHEN p.probability IS NULL          THEN 'NO_PINNACLE_PROBABILITY_ON_THE_VALUATION'
              WHEN p.decided_at IS NULL           THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_DECISION_TIME'
              WHEN p.in_live_window               THEN NULL
              WHEN NOT p.has_receipt              THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PRICE_RECEIPT_TIME'
              WHEN p.observed_at IS NULL          THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PINNACLE_OBSERVED_AT'
         END AS reason
    FROM parsed p
    CROSS JOIN st
    LEFT JOIN lf ON lf.cohort = p.cohort AND lf.fixture = p.fixture
), fx AS (
  SELECT cohort, fixture,
         min(CASE WHEN fixture_in_training THEN 1
                  WHEN NOT observed AND reason IS NULL AND walk = 'PENDING_BACKFILL' THEN 2
                  WHEN NOT observed AND reason IS NULL AND walk = 'IN_LIVE_WINDOW' THEN 3
                  WHEN NOT observed AND reason IS NULL THEN 4
                  WHEN observed THEN 5
                  ELSE 6 END) AS standing
    FROM cv
   WHERE labelled AND fixture IS NOT NULL
   GROUP BY 1, 2
)
SELECT c.cohort,
       count(fx.fixture) AS labelled_stored_fixtures_upper_bound,
       count(*) FILTER (WHERE fx.standing = 1) AS in_training_now,
       count(*) FILTER (WHERE fx.standing = 2) AS pending_backfill,
       count(*) FILTER (WHERE fx.standing = 3) AS in_live_window,
       count(*) FILTER (WHERE fx.standing = 4) AS observable_but_walked_past_gap,
       count(*) FILTER (WHERE fx.standing = 5) AS observed_label_outside_cutoff,
       count(*) FILTER (WHERE fx.standing = 6) AS every_labelled_row_refused,
       count(*) FILTER (WHERE fx.standing IN (1, 2, 3)) AS projected_after_backfill,
       40 AS min_train_events,                       -- bettor_funded_model.MIN_TRAIN_EVENTS
       greatest(0, 40 - count(*) FILTER (WHERE fx.standing IN (1, 2, 3)))
           AS projected_shortfall
  FROM (VALUES ('DISPLAYED_PRICE_AGE_UNKNOWN'), ('EXECUTABLE_PRICE_CURRENT')) AS c(cohort)
  LEFT JOIN fx ON fx.cohort = c.cohort
 GROUP BY c.cohort
 ORDER BY c.cohort;

\echo '== B4a · stored valuations NOT observed, by the observer''s named reason =='
-- reason NULL = none derivable: the row would be observed (B1b says when).
-- distinct_fixtures excludes rows with no fixture identity.
-- labelled_fixtures_not_in_training: labelled fixtures this reason keeps out
-- of B3a (no other labelled observation of the fixture in the cohort).
WITH st AS (
  SELECT max(CASE WHEN jsonb_typeof(value -> 'cursor_below_id') = 'number'
                  THEN (value ->> 'cursor_below_id')::numeric END) AS cursor_below_id,
         coalesce(bool_or(value -> 'exhausted' = 'true'::jsonb), false) AS exhausted
    FROM ingestion_state WHERE key = 'derek_research_backfill'
), lf AS (
  SELECT DISTINCT o.cohort, o.fixture
    FROM derek_research_observations o
    JOIN external_valuations v ON v.id = o.valuation_id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose = o.record_purpose
     AND v.outcome_known AND v.outcome IN (0, 1)
     AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
     AND v.outcome_at IS NOT NULL
     AND o.decided_at <= now() AND v.outcome_at <= now()
), raw AS (
  SELECT v.id, v.record_purpose, v.decided_at, v.observed_at, v.probability,
         v.executable_price,
         CASE v.record_purpose WHEN 'CALIBRATION_ONLY' THEN 'DISPLAYED_PRICE_AGE_UNKNOWN'
                               WHEN 'ENTRY_DECISION'   THEN 'EXECUTABLE_PRICE_CURRENT' END AS cohort,
         CASE WHEN coalesce(v.condition_id, '')   <> '' THEN 'condition:' || v.condition_id
              WHEN coalesce(v.event_key, '')      <> '' THEN 'event:' || v.event_key
              WHEN coalesce(v.us_market_slug, '') <> '' THEN 'slug:' || v.us_market_slug END AS fixture,
         CASE WHEN v.record_purpose = 'CALIBRATION_ONLY' THEN coalesce(
                NULLIF(v.calibration_only_evidence #> '{compared_at_the_displayed_price,price}', 'null'::jsonb),
                v.calibration_only_evidence #> '{displayed_quote,acquisition_price}') END AS price_j,
         CASE v.record_purpose
           WHEN 'CALIBRATION_ONLY' THEN v.calibration_only_evidence #> '{displayed_quote,read_at}'
           WHEN 'ENTRY_DECISION'   THEN v.risk_verdict #> '{freshness_evidence,venue_clock,our_response_received_at}'
         END AS receipt_j,
         (v.outcome_known AND v.outcome IN (0, 1)
          AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
          AND v.outcome_at IS NOT NULL) AS labelled,
         EXISTS (SELECT 1 FROM derek_research_observations o
                  WHERE o.valuation_id = v.id) AS observed
    FROM external_valuations v
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose IN ('CALIBRATION_ONLY', 'ENTRY_DECISION')
), parsed AS (
  SELECT r.*,
         CASE WHEN r.record_purpose = 'ENTRY_DECISION' THEN
                CASE WHEN r.executable_price IS NULL THEN 'NONE'
                     WHEN r.executable_price > 0 AND r.executable_price < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'number' THEN
                CASE WHEN (r.price_j #>> '{}')::numeric > 0
                      AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                     ELSE 'OUT_OF_RANGE' END
              WHEN jsonb_typeof(r.price_j) = 'boolean' THEN 'OUT_OF_RANGE'
              WHEN jsonb_typeof(r.price_j) = 'string' THEN
                CASE WHEN (r.price_j #>> '{}') ~* '^\s*[-+]?(nan|inf|infinity)\s*$' THEN 'OUT_OF_RANGE'
                     WHEN (r.price_j #>> '{}') ~ '^\s*[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?\s*$' THEN
                       CASE WHEN (r.price_j #>> '{}')::numeric > 0
                             AND (r.price_j #>> '{}')::numeric < 1 THEN 'IN_RANGE'
                            ELSE 'OUT_OF_RANGE' END
                     ELSE 'NONE' END
              ELSE 'NONE' END AS price_class,
         CASE jsonb_typeof(r.receipt_j)
           WHEN 'number'  THEN true
           WHEN 'boolean' THEN true
           WHEN 'string'  THEN (r.receipt_j #>> '{}')
                ~* '^\s*[-+]?(nan|inf|infinity|([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?)\s*$'
           ELSE false END AS has_receipt,
         r.decided_at > now() - interval '1800 seconds' AS in_live_window
    FROM raw r
), cv AS (
  SELECT p.*,
         (lf.fixture IS NOT NULL) AS fixture_in_training,
         CASE WHEN p.observed THEN 'OBSERVED'
              WHEN p.in_live_window THEN 'IN_LIVE_WINDOW'
              WHEN NOT st.exhausted
                   AND (st.cursor_below_id IS NULL OR p.id < st.cursor_below_id)
                   THEN 'PENDING_BACKFILL'
              ELSE 'WALKED_PAST' END AS walk,
         CASE WHEN p.fixture IS NULL              THEN 'NO_FIXTURE_IDENTITY_ON_THE_VALUATION'
              WHEN p.price_class = 'NONE'         THEN 'NO_PRICE_ON_THE_VALUATION'
              WHEN p.price_class = 'OUT_OF_RANGE' THEN 'PRICE_NOT_STRICTLY_BETWEEN_0_AND_1'
              WHEN p.probability IS NULL          THEN 'NO_PINNACLE_PROBABILITY_ON_THE_VALUATION'
              WHEN p.decided_at IS NULL           THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_DECISION_TIME'
              WHEN p.in_live_window               THEN NULL
              WHEN NOT p.has_receipt              THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PRICE_RECEIPT_TIME'
              WHEN p.observed_at IS NULL          THEN 'BACKFILL_EXCLUDED_STORED_ROW_HOLDS_NO_PINNACLE_OBSERVED_AT'
         END AS reason
    FROM parsed p
    CROSS JOIN st
    LEFT JOIN lf ON lf.cohort = p.cohort AND lf.fixture = p.fixture
)
SELECT record_purpose, walk, reason AS observer_reason,
       count(*) AS rows, count(DISTINCT fixture) AS distinct_fixtures,
       count(*) FILTER (WHERE labelled) AS labelled_rows,
       count(DISTINCT fixture) FILTER (WHERE labelled) AS labelled_fixtures,
       count(DISTINCT fixture) FILTER (WHERE labelled AND NOT fixture_in_training)
           AS labelled_fixtures_not_in_training
  FROM cv
 WHERE NOT observed
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, (reason IS NULL) DESC, 4 DESC;

\echo '== B4b · research observations by label state (settled-but-unlabelled named) =='
-- LABELLED is B3a. Every other state is NOT a label (derek_research.LABEL_SQL):
-- a void, an outcome without a verified venue basis, an unjoined outcome.
-- NOT_YET_SETTLED is pending, not an exclusion.
-- fixtures_without_any_label: fixtures in this state with no labelled
-- observation in the cohort (what the state actually costs the fit).
WITH lf AS (
  SELECT DISTINCT o.cohort, o.fixture
    FROM derek_research_observations o
    JOIN external_valuations v ON v.id = o.valuation_id
   WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose = o.record_purpose
     AND v.outcome_known AND v.outcome IN (0, 1)
     AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
     AND v.outcome_at IS NOT NULL
     AND o.decided_at <= now() AND v.outcome_at <= now()
), s AS (
  SELECT o.cohort, o.collection_mode, o.fixture,
         CASE WHEN v.id IS NULL
                   THEN 'OUTCOME_NOT_JOINED: no entry-experiment valuation with this id'
              WHEN v.record_purpose IS DISTINCT FROM o.record_purpose
                   THEN 'OUTCOME_NOT_JOINED: the valuation''s record_purpose differs'
              WHEN v.outcome_known AND v.outcome IN (0, 1)
                   AND v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'])
                   AND v.outcome_at IS NOT NULL
                   THEN CASE WHEN o.decided_at <= now() AND v.outcome_at <= now()
                             THEN 'LABELLED'
                             ELSE 'LABELLED_AFTER_THE_READ_INSTANT' END
              WHEN v.outcome_basis = 'CONFIRMED_VOID'
                   THEN 'VOID: CONFIRMED_VOID (no 0/1 truth)'
              WHEN v.outcome_known AND v.outcome_basis IS NULL
                   THEN 'UNVERIFIED_BASIS: outcome written without a basis'
              WHEN v.outcome_known
                   AND NOT (v.outcome_basis = ANY (ARRAY['VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME']))
                   THEN 'UNVERIFIED_BASIS: ' || v.outcome_basis
              WHEN v.outcome_known
                   THEN 'OUTCOME_KNOWN_WITHOUT_0_1_OR_TIME'
              WHEN v.outcome_basis IS NOT NULL
                   THEN 'BASIS_WITHOUT_A_KNOWN_OUTCOME: ' || v.outcome_basis
              ELSE 'NOT_YET_SETTLED (pending)' END AS label_state
    FROM derek_research_observations o
    LEFT JOIN external_valuations v
           ON v.id = o.valuation_id
          AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
)
SELECT s.cohort, s.collection_mode, s.label_state,
       count(*) AS rows, count(DISTINCT s.fixture) AS distinct_fixtures,
       count(DISTINCT s.fixture) FILTER (WHERE lf.fixture IS NULL)
           AS fixtures_without_any_label
  FROM s
  LEFT JOIN lf ON lf.cohort = s.cohort AND lf.fixture = s.fixture
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, (s.label_state = 'LABELLED') DESC, 4 DESC;

\echo '== B5a · derek_research_model_runs, last 7 UTC days (an INSUFFICIENT row no longer closes the day: 181) =='
SELECT run_day, count(*) AS runs,
       string_agg(run_id || '=' || outcome, ' , ' ORDER BY ran_at, recorded_at)
           AS runs_in_order,
       count(*) FILTER (WHERE outcome <> 'INSUFFICIENT_LABELLED_FIXTURES')
           AS decisive_runs
  FROM derek_research_model_runs
 WHERE run_day >= (now() AT TIME ZONE 'UTC')::date - 6
 GROUP BY run_day ORDER BY run_day DESC;

\echo '== B5b · derek_research_model_runs, newest 10 =='
SELECT run_id, run_day, ran_at, outcome,
       counts #>> '{by_cohort,DISPLAYED_PRICE_AGE_UNKNOWN,have_labelled_fixtures}'
           AS displayed_have,
       counts #>> '{by_cohort,EXECUTABLE_PRICE_CURRENT,have_labelled_fixtures}'
           AS executable_have,
       counts ->> 'need_labelled_training_fixtures' AS need,
       left(counts ->> 'error', 120) AS labels_error,
       attempted_model_ids,
       CASE WHEN jsonb_typeof(fitted) = 'object' THEN
            (SELECT string_agg(e.k || '=' || coalesce(e.f ->> 'outcome', 'NO_REFIT')
                               || coalesce(' (' || left(coalesce(e.f ->> 'refusal',
                                                                 e.f ->> 'why'), 90) || ')', ''),
                               ' ; ' ORDER BY e.k)
               FROM jsonb_each(fitted) AS e(k, f)) END AS fitted_by_cohort,
       detail #> '{same_day_rerun,after_run_ids}' AS same_day_rerun_after,
       promoted, recorded_at
  FROM derek_research_model_runs
 ORDER BY run_day DESC, ran_at DESC, recorded_at DESC
 LIMIT 10;

\echo '== B5c · derek_research_model_attempts by outcome, then newest 10 =='
SELECT cohort, outcome, count(*) AS attempts, max(attempted_at) AS last_attempted_at
  FROM derek_research_model_attempts
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT a.attempt_id, a.run_id,
       EXISTS (SELECT 1 FROM derek_research_model_runs r WHERE r.run_id = a.run_id)
           AS run_row_written,
       a.cohort, a.attempted_at, a.fit_through, a.outcome,
       a.train_rows, a.train_fixtures,
       left(a.refusal, 160) AS refusal,
       left(a.records_sha, 12) AS records_sha_12,
       CASE WHEN jsonb_typeof(a.evaluation_cohort -> 'declared_at_epoch_s') = 'number'
            THEN to_timestamp((a.evaluation_cohort ->> 'declared_at_epoch_s')::float8) END
           AS evaluation_cohort_declared_at,
       a.evaluation_cohort -> 'cohorts' AS evaluation_cohorts,
       a.evaluation_cohort ->> 'evidence_class' AS evaluation_evidence_class,
       a.detail ->> 'degenerate_because' AS degenerate_because,
       a.recorded_at
  FROM derek_research_model_attempts a
 ORDER BY a.attempted_at DESC, a.recorded_at DESC
 LIMIT 10;

\echo '== B6a · research-source models in bettor_funded_models, by state =='
SELECT state, count(*) AS models, max(created_at) AS newest_created_at
  FROM bettor_funded_models
 WHERE model_key = 'derek_entry_payout_event'
   AND training_provenance ->> 'source' = 'DEREK_RESEARCH_OBSERVATIONS'
 GROUP BY state ORDER BY state;

\echo '== B6b · CANDIDATE research-source models (newest 10) =='
-- paper_derek_would_try: paper_derek.research_model reads the 10 newest
-- CANDIDATE derek_entry_payout_event models created by now and takes the
-- first fitted on DEREK_RESEARCH_OBSERVATIONS. It then RE-VERIFIES the
-- provenance (re-reads and re-hashes every training record) -- that check
-- is Python's and is not repeated here; this column says which model it
-- would try, not that the check passes.
WITH c AS (
  SELECT m.*,
         row_number() OVER (ORDER BY m.created_at DESC) AS candidate_rank,
         (m.training_provenance ->> 'source' = 'DEREK_RESEARCH_OBSERVATIONS') AS research
    FROM bettor_funded_models m
   WHERE m.model_key = 'derek_entry_payout_event'
     AND m.state = 'CANDIDATE'
     AND m.created_at <= now()
), r AS (
  SELECT c.*, row_number() OVER (ORDER BY c.created_at DESC) AS research_rank
    FROM c WHERE c.research
)
SELECT r.model_id, r.state, r.created_at, r.fit_through,
       r.trained_through, r.outcomes_available_through, r.train_rows,
       r.training_provenance -> 'n_events' AS n_events,
       r.training_provenance -> 'n_rows' AS n_rows,
       r.training_provenance #> '{training_population,training_cohorts}' AS training_cohorts,
       r.training_provenance #> '{training_population,evidence_class_mix}' AS evidence_class_mix,
       r.training_provenance #> '{windows,evaluation_cohort,cohorts}' AS evaluation_cohorts,
       r.training_provenance #>> '{windows,declared_before_fitting}' AS declared_before_fitting,
       left(r.training_provenance ->> 'records_sha', 12) AS records_sha_12,
       a.outcome AS attempt_outcome,
       r.evaluation IS NOT NULL AS has_stored_evaluation,
       (r.research_rank = 1 AND r.candidate_rank <= 10) AS paper_derek_would_try
  FROM r
  LEFT JOIN derek_research_model_attempts a ON a.attempt_id = r.model_id
 ORDER BY r.created_at DESC
 LIMIT 10;

ROLLBACK;
