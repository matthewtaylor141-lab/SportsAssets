-- READ-ONLY EV DIAGNOSTIC. Nothing here arms, writes or mutates.
--
-- WHY IT IS A SEPARATE JOB AND NOT `always()` ON THE EXISTING STEP.
-- `verify` step 22 ("External valuation -- arm, cycle, and read production
-- back") is skipped when step 20 fails, and step 20's actual failure is
-- `NO_COVERED_MARKET_COULD_SUPPLY_A_COMPLETE_ENTRY` -- the RN1X ACCEPTANCE
-- FIXTURE had no qualifying subject. Independent production evidence was
-- therefore suppressed by an unrelated fixture.
--
-- Wrapping step 22 in `always()` would be the wrong repair: that step ARMS
-- the lane and drives cycles, so forcing it to run after an unexplained
-- failure would mutate the system precisely when the state is least
-- understood. This file reads instead, and it is run by a job with no
-- `needs:` so no other job's outcome can gate it.
--
-- AND IT WAS READING THE WRONG BUILD ANYWAY. That verify job queries the
-- SERVING API, which is `c3d0cfc` -- not the release code under test. A
-- diagnostic must say which build produced each number, which section 1
-- does.

\echo '=== 1 · WHICH BUILD WROTE WHAT, AND WHEN THE CYCLE RAN ==='
-- `cycle_started_at` is derived: the heartbeat stamps completion (`at`) and
-- carries the elapsed seconds, so start = at - elapsed. Both are printed
-- rather than only the difference, because a derived instant should be
-- checkable.
SELECT key,
       (value::jsonb -> 'writer' ->> 'build')            AS cycle_writer_sha,
       (value::jsonb -> 'writer' ->> 'source_sha256_12')  AS writer_module_digest,
       (value::jsonb -> 'writer' ->> 'pid')               AS writer_pid,
       to_timestamp((value::jsonb ->> 'at')::float8)      AS cycle_finished_at,
       (value::jsonb ->> 'elapsed_s')                     AS elapsed_s,
       to_timestamp((value::jsonb ->> 'at')::float8
                    - coalesce((value::jsonb ->> 'elapsed_s')::float8, 0))
                                                          AS cycle_started_at,
       (value::jsonb ->> 'state')                         AS state,
       (value::jsonb ->> 'cycle_label')                   AS cycle_label
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby')
 ORDER BY key;

\echo '=== 2 · ZERO CANDIDATES vs FAILED ACQUISITION -- NOT THE SAME ==='
-- THE DISTINCTION, STATED AS A VERDICT RATHER THAN LEFT TO A READER.
--   NO_GLOBAL_FIXTURES        the catalogue had nothing open and fresh
--   PROVIDER_RETURNED_NOTHING we asked and got no events
--   PROVIDER_ERROR            the acquisition itself failed
--   CANDIDATES_ALL_REFUSED    candidates existed and each was refused
-- Counting an acquisition failure as "no opportunity" is the specific
-- error this section exists to prevent.
WITH h AS (SELECT value::jsonb AS v FROM ingestion_state
            WHERE key = 'ext_pinnacle_last_cycle')
SELECT (v ->> 'markets_considered')                     AS global_fixtures,
       (SELECT coalesce(sum((f.value ->> 'provider_events')::int), 0)
          FROM jsonb_each(coalesce(v -> 'funnel_by_provider_sport',
                                   '{}'::jsonb)) AS f)  AS provider_events,
       (v -> 'refusals' ->> 'PROVIDER_ERROR')            AS provider_errors,
       (v -> 'refusals' ->> 'NO_CANDIDATE_MARKETS')      AS no_candidate_markets,
       jsonb_array_length(coalesce(v -> 'mapped_candidate_ledger',
                                   '[]'::jsonb))         AS candidates_ledgered,
       (v ->> 'evaluated')                               AS evaluated,
       CASE
         WHEN coalesce((v -> 'refusals' ->> 'PROVIDER_ERROR')::int, 0) > 0
              THEN 'PROVIDER_ERROR'
         WHEN coalesce((v ->> 'markets_considered')::int, 0) = 0
              THEN 'NO_GLOBAL_FIXTURES'
         WHEN (SELECT coalesce(sum((f.value ->> 'provider_events')::int), 0)
                 FROM jsonb_each(coalesce(v -> 'funnel_by_provider_sport',
                                          '{}'::jsonb)) AS f) = 0
              THEN 'PROVIDER_RETURNED_NOTHING'
         ELSE 'CANDIDATES_ALL_REFUSED'
       END                                               AS verdict
  FROM h;

\echo '=== 3 · EACH CANDIDATE, ITS FIRST BLOCKING CONDITION, IN FULL ==='
WITH h AS (SELECT value::jsonb -> 'mapped_candidate_ledger' AS l
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT jsonb_pretty(e) AS candidate
  FROM h, jsonb_array_elements(coalesce(h.l, '[]'::jsonb)) AS e;

\echo '=== 4 · OVERLAPPING DOWNSTREAM REFUSALS -- a cause and its effect ==='
-- The cycle's refusal tally counts EVERY refusal a candidate met, so a
-- cause and its downstream consequence both appear. The ledger counts ONE
-- per candidate. Printing both, side by side, is what stops the inventory
-- being inflated by double counting.
WITH h AS (SELECT value::jsonb AS v FROM ingestion_state
            WHERE key = 'ext_pinnacle_last_cycle'),
     tally AS (SELECT k AS code, (h.v -> 'refusals' ->> k)::int AS tally_count
                 FROM h, jsonb_object_keys(coalesce(h.v -> 'refusals',
                                                    '{}'::jsonb)) AS k),
     firsts AS (SELECT (e ->> 'first_refusal') AS code, count(*)::int AS as_first
                  FROM h, jsonb_array_elements(
                         coalesce(h.v -> 'mapped_candidate_ledger',
                                  '[]'::jsonb)) AS e
                 GROUP BY 1)
SELECT coalesce(t.code, f.code)                  AS refusal_code,
       t.tally_count                             AS times_met_anywhere,
       coalesce(f.as_first, 0)                   AS times_it_was_the_first,
       CASE WHEN coalesce(f.as_first, 0) = 0 AND coalesce(t.tally_count, 0) > 0
            THEN 'DOWNSTREAM_ONLY -- never the first blocker for any candidate'
            ELSE 'BLOCKING for at least one candidate' END AS reading
  FROM tally t FULL OUTER JOIN firsts f ON f.code = t.code
 ORDER BY coalesce(t.tally_count, 0) DESC;

\echo '=== 5 · FUNDED ACCOUNT: UNBOUND vs A FAILED READ ==='
-- THE DISTINCTION MATTERS AND THE OLD READBACK BLURRED IT. An absent
-- binding row is a real, knowable state ("nothing is bound"). A read that
-- FAILS is not that -- it is no information. The first is a decision the
-- owner has not made; the second is a defect on our side.
SELECT 'authorization'                    AS record,
       count(*)                           AS rows_present,
       CASE WHEN count(*) = 0 THEN 'ABSENT -- no funded authorization exists'
            ELSE 'PRESENT' END            AS reading
  FROM ingestion_state WHERE key = 'bettor_funded_authorization'
UNION ALL
SELECT 'approved_limits', count(*),
       CASE WHEN count(*) = 0 THEN 'ABSENT -- no approved limit set exists'
            ELSE 'PRESENT' END
  FROM ingestion_state WHERE key = 'bettor_funded_limits_approved'
UNION ALL
SELECT 'desk_accounts', count(*),
       CASE WHEN count(*) = 0 THEN 'ABSENT -- no account is registered'
            ELSE 'PRESENT' END
  FROM bettor_desk_accounts;

-- THE REGISTERED ACCOUNT'S OWN STATE, which decides eligibility.
SELECT account_id, desk_id, status, paused, accounting_status, opening_balance
  FROM bettor_desk_accounts
 ORDER BY account_id;

\echo '=== 6 · SERVICING RESULT, AND WHY IT MIGHT BE ABSENT ==='
-- `funded_servicing=null` had ONE meaning for several causes. This
-- separates them: an empty book has nothing to service, which is not the
-- same as a servicing pass that failed or a build that never wrote it.
WITH h AS (SELECT value::jsonb AS v FROM ingestion_state
            WHERE key = 'ext_pinnacle_last_cycle'),
     b AS (SELECT count(*) AS positions FROM bettor_funded_intents
            WHERE closed_at IS NULL)
SELECT ((h.v -> 'funded_servicing') IS NOT NULL)      AS servicing_key_present,
       (h.v -> 'funded_servicing' ->> 'ok')            AS servicing_ok,
       (h.v -> 'funded_servicing' ->> 'refusal')       AS servicing_refusal,
       (h.v -> 'funded_servicing' ->> 'positions_serviced')
                                                       AS positions_serviced,
       b.positions                                     AS open_positions_in_book,
       CASE
         WHEN (h.v -> 'funded_servicing') IS NULL AND b.positions = 0
              THEN 'NOTHING TO SERVICE -- the funded book is empty'
         WHEN (h.v -> 'funded_servicing') IS NULL AND b.positions > 0
              THEN 'DEFECT -- positions exist and no servicing was recorded'
         ELSE 'SERVICING RECORDED' END                 AS reading
  FROM h, b;

\echo '=== 7 · DO ANY ORDERS OR HOLDINGS EXIST AT ALL? ==='
SELECT 'funded_intents'  AS ledger, count(*) AS rows_,
       count(*) FILTER (WHERE state = 'FILLED')   AS filled,
       count(*) FILTER (WHERE closed_at IS NULL)  AS open_
  FROM bettor_funded_intents
UNION ALL
SELECT 'funded_fills', count(*), 0, 0 FROM bettor_funded_fills
UNION ALL
SELECT 'external_valuations', count(*),
       count(*) FILTER (WHERE admissible), 0 FROM external_valuations;

\echo '=== 8 · THE CONTROL ROWS ==='
SELECT key, left(value::text, 120) AS value_head
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'ext_pinnacle_shadow',
               'bettor_live_loop_armed', 'funded_exit_enabled')
 ORDER BY key;
